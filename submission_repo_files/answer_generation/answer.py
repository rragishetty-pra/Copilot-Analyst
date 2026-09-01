"""
Core answer-generation orchestration: question -> retrieval -> model routing
-> grounded LLM call -> citation validation -> formatted result.

This is the module that actually implements the DNA doc's Core Principles
(§2) and Answer Behavior Rules (§4) end to end — everything upstream
(ingestion, retrieval) exists to feed this, and everything downstream (CLI,
Flask app) exists to render what this returns.
"""
import logging
import time
import uuid

from vectorstore import local_store
from embeddings import embedder
from security.access_log import log_event
from config import CONFLICT_SCORE_GAP_THRESHOLD, DEFAULT_TOP_K

from . import llm_client, session_store, doc_hints
from .prompts import build_system_prompt, SUBMIT_ANSWER_TOOL
from .routing import choose_model

logger = logging.getLogger(__name__)


def _retrieve(question: str, top_k: int) -> tuple[list[dict], str]:
    """Retrieval with a document-name-aware boost (see doc_hints.py): if the
    question names a company that matches a document in the corpus, search
    within that document first — plain corpus-wide cosine similarity was
    found during testing to let generic repeated financial phrases in one
    document's tables outrank the actually-relevant document entirely.
    Falls back to corpus-wide search if the hinted document doesn't have
    enough relevant chunks on its own. Returns (chunks, retrieval_mode) —
    the mode is logged for later analysis of how often the hint fires.

    Spell-corrects the question BEFORE doc_hints sees it — doc_hints'
    company-name detection is a plain substring check against the raw text,
    so a misspelled company name ("nkie") would never match "NIKE" and the
    doc-scoped-retrieval boost would silently never fire for it. embed_query
    (called inside local_store.query) also corrects internally, so this
    isn't required for the TF-IDF similarity itself to work — but doc_hints
    has no equivalent step of its own, so correcting here is what lets a
    misspelled company name actually unlock doc-scoped retrieval rather
    than falling back to corpus-wide search and risking the same dilution
    problem the CEO-lookup bug had."""
    question = embedder.correct_query_spelling(question)
    hinted_doc_ids = doc_hints.detect_mentioned_doc_ids(question)
    if not hinted_doc_ids:
        return local_store.query(question, top_k=top_k), "corpus_wide"

    scoped_hits: list[dict] = []
    for doc_id in hinted_doc_ids:
        scoped_hits.extend(local_store.query(question, top_k=top_k, doc_id=doc_id))
    scoped_hits.sort(key=lambda c: c["score"], reverse=True)
    scoped_hits = scoped_hits[:top_k]

    if len(scoped_hits) >= 2:
        return scoped_hits, f"doc_scoped:{','.join(hinted_doc_ids)}"

    # Not enough in the hinted document(s) — don't force a bad answer from a
    # thin result set; fall back to corpus-wide search instead.
    return local_store.query(question, top_k=top_k), "corpus_wide_fallback"


def _validate_citations(citations: list[dict], retrieved_chunks: list[dict]) -> tuple[bool, list[str]]:
    """Cross-checks every citation the model claims against the chunks it was
    actually given. This is Core Rule #4 (a hallucinated-but-correct answer
    is still a failure) made executable rather than just prompted for — if
    the model cites a document/page combination that wasn't in the retrieved
    set, that citation is unverifiable and the answer can't stand as given."""
    retrieved_keys = {(c["doc_name"], c["page_number"]) for c in retrieved_chunks}
    problems = []
    for cit in citations:
        key = (cit.get("doc_name"), cit.get("page_number"))
        if key not in retrieved_keys:
            problems.append(
                f"cited {cit.get('doc_name')} p.{cit.get('page_number')} was not in the retrieved excerpts"
            )
    return (len(problems) == 0), problems


def _detect_retrieval_conflict(chunks: list[dict]) -> bool:
    """Cheap pre-check: are the top chunks close enough in score that no
    single source clearly dominates? This doesn't decide there IS a
    conflict (the LLM does that, since it can tell if the chunks actually
    disagree in content) — it's just a signal logged alongside the answer
    for later analysis of how often near-ties come up in practice."""
    if len(chunks) < 2:
        return False
    return (chunks[0]["score"] - chunks[1]["score"]) < CONFLICT_SCORE_GAP_THRESHOLD


def ask(question: str, session_id: str | None = None, top_k: int = DEFAULT_TOP_K, user: str = "single_user") -> dict:
    """Answer one question end to end. Returns a dict with at minimum a
    `status` field; shape otherwise matches the submit_answer tool schema,
    plus `model_used`, `model_routing_reason`, and `retrieved_count`."""
    session_id = session_id or str(uuid.uuid4())
    t_start = time.time()

    # Pull conversational history BEFORE retrieval (not just before the LLM
    # call, as originally ordered) — see retrieval_query below for why.
    history = session_store.get_history(session_id)

    # Bug found during testing: a follow-up like "what about in 2021?" or
    # "and for Costco?" carries almost no retrievable keywords on its own —
    # TF-IDF has nothing to match, _retrieve() comes back empty or grabs
    # whatever unrelated documents happen to share a stray word (a bare
    # year like "2021"), and the question either short-circuits to
    # "not found" or answers off the wrong document — *before the LLM ever
    # sees the conversation history*. The history was being threaded into
    # the LLM prompt correctly, but retrieval never got a chance to use it,
    # so the conversation looked like it wasn't continuing at all.
    #
    # Attempt 1: prepend only the single most recent user turn. Broke on a
    # THIRD question in a chain of elliptical follow-ups ("Nike revenue
    # 2023?" -> "what about 2021?" -> "what about 2019?"): by turn 3 the
    # "most recent turn" (turn 2) is itself elliptical and never mentioned
    # Nike, so the entity got lost one hop later than accounted for.
    #
    # Attempt 2: join ALL prior user turns, unconditionally. Fixed the
    # chain above, but broke the moment the user actually changed topics
    # mid-session (e.g. asking about Pfizer after several Nike follow-ups)
    # — confirmed directly: the accumulated Nike terms outweighed the new,
    # single Pfizer mention and Nike chunks still dominated the results
    # even though doc_hints correctly identified Pfizer too.
    #
    # Fix: only borrow prior turns when the CURRENT question can't stand on
    # its own. doc_hints.detect_mentioned_doc_ids() run on the current
    # question is a cheap, reliable signal for "this question already names
    # a company" — when it fires, trust it exclusively (a fresh, explicit
    # mention should always win over inherited context); only fall back to
    # the full accumulated history when the current question alone doesn't
    # resolve to anything. Verified against a 4-turn chain (3 elliptical
    # Nike follow-ups, then a self-sufficient Pfizer question): the Nike
    # chain still narrows by year correctly, and the Pfizer question now
    # scopes purely to Pfizer with zero Nike bleed-through.
    #
    # Spell-corrected before the check (not just the raw question) — a
    # misspelled company name ("nkie") is still a self-sufficient mention
    # once corrected, and should be treated as one rather than triggering
    # an unnecessary (and, per the topic-change bug above, sometimes
    # actively harmful) history-borrowing fallback.
    #
    # This does NOT change what's sent to the LLM (`question` and the full
    # `history` below are untouched) — only what retrieval searches for.
    retrieval_query = question
    corrected_question = embedder.correct_query_spelling(question)
    if history and not doc_hints.detect_mentioned_doc_ids(corrected_question):
        prior_user_turns = [h["content"] for h in history if h["role"] == "user"]
        if prior_user_turns:
            retrieval_query = f"{' '.join(prior_user_turns)} {question}"

    retrieved, retrieval_mode = _retrieve(retrieval_query, top_k)

    # DNA doc Core Rule #2: "not found" is the default the moment retrieval
    # fails to surface a supporting passage. Originally this also short-
    # circuited on a numeric score threshold (NOT_FOUND_SCORE_THRESHOLD) —
    # removed after testing showed TF-IDF cosine scores are NOT a reliable
    # relevance signal on their own: a genuinely correct Nike answer scored
    # below the threshold (natural-language phrasing dilutes the query
    # vector), while a nonsense query ("what is the capital of France")
    # scored 0.894 against an Adobe chunk dominated by the common financial
    # term "capital". The LLM's own closed-book judgment proved reliable in
    # every test (correctly refusing outside knowledge, correctly calling
    # not_found/partial) — so that's the actual not-found authority now.
    # Only the genuinely-empty case (nothing retrieved at all) short-circuits.
    if not retrieved:
        result = {
            "status": "not_found",
            "answer": None,
            "citations": [],
            "reasoning_trace": "No passages were retrieved for this question at all.",
            "model_used": None,
            "model_routing_reason": "skipped LLM call — nothing retrieved",
            "retrieved_count": 0,
            "retrieval_mode": retrieval_mode,
            "session_id": session_id,
            "elapsed_seconds": round(time.time() - t_start, 2),
        }
        log_event("query", user=user, question=question, session_id=session_id,
                  status="not_found", retrieved_count=0, retrieval_mode=retrieval_mode)
        return result

    model, routing_reason = choose_model(question, retrieved)
    near_tie_signal = _detect_retrieval_conflict(retrieved)

    system_prompt = build_system_prompt(retrieved)
    messages = history + [{"role": "user", "content": question}]

    try:
        response_body = llm_client.call_model(
            system=system_prompt,
            messages=messages,
            tools=[SUBMIT_ANSWER_TOOL],
            tool_choice={"type": "tool", "name": "submit_answer"},
            model=model,
        )
        parsed = llm_client.extract_tool_input(response_body, "submit_answer")
    except llm_client.LLMError as e:
        logger.error("Answer generation failed for question %r: %s", question, e)
        log_event("query_error", user=user, question=question, session_id=session_id, error=str(e))
        return {
            "status": "error",
            "answer": None,
            "citations": [],
            "reasoning_trace": None,
            "error": str(e),
            "model_used": model,
            "model_routing_reason": routing_reason,
            "retrieved_count": len(retrieved),
            "retrieval_mode": retrieval_mode,
            "session_id": session_id,
            "elapsed_seconds": round(time.time() - t_start, 2),
        }

    # Citation grounding check (Core Rule #4) — downgrade rather than trust
    # an unverifiable citation.
    citations = parsed.get("citations") or []
    ok, problems = _validate_citations(citations, retrieved)

    # The model cites doc_name + page_number (that's what it can see in the
    # excerpts); the frontend's citation viewer needs doc_id to fetch the
    # right PDF. Enrich each citation from the retrieved-chunk metadata now
    # that we've confirmed every citation matches something actually
    # retrieved, rather than asking the model to track an id it never uses.
    if ok:
        doc_id_by_key = {(c["doc_name"], c["page_number"]): c["doc_id"] for c in retrieved}
        for cit in citations:
            cit["doc_id"] = doc_id_by_key.get((cit.get("doc_name"), cit.get("page_number")))

    if not ok and parsed.get("status") in ("answered", "partial"):
        logger.warning("Citation validation failed for question %r: %s", question, problems)
        parsed["status"] = "not_found"
        parsed["answer"] = None
        parsed["citations"] = []
        parsed["reasoning_trace"] = (
            (parsed.get("reasoning_trace") or "")
            + f" [citation validation failed: {'; '.join(problems)} — answer withheld]"
        )

    result = {
        **parsed,
        "model_used": model,
        "model_routing_reason": routing_reason,
        "near_tie_signal": near_tie_signal,
        "retrieved_count": len(retrieved),
        "retrieval_mode": retrieval_mode,
        "session_id": session_id,
        "elapsed_seconds": round(time.time() - t_start, 2),
    }

    # Persist conversational memory — store plain text, not the tool-call
    # apparatus (see session_store.py docstring for why).
    session_store.append_turn(session_id, "user", question)
    session_store.append_turn(session_id, "assistant", result.get("answer") or f"[{result['status']}]")

    log_event(
        "query", user=user, question=question, session_id=session_id,
        status=result["status"], model_used=model, retrieved_count=len(retrieved), retrieval_mode=retrieval_mode,
        retrieved_chunk_ids=[c["chunk_id"] for c in retrieved],
        citations=citations,
    )
    return result
