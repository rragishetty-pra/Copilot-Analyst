"""
System prompt + structured-output tool schema for the answer-generation
layer. This is where the DNA doc's Core Principles (§2), answer-behavior
rules (§4), and Standard Answer Format (§5) actually get enforced — the
prompt is the mechanism, not just documentation of intent.

Design choice: force the model to respond via a single tool call
("submit_answer") with a strict JSON schema, rather than free text. Two
reasons:
  1. It's parseable — the app layer needs structured fields (citations,
     status) to render the Standard Answer Format and to build the
     clarifying-question UX, not prose to scrape.
  2. It's a grounding check. Every citation the model claims is validated
     against the actual retrieved chunk set after the call returns (see
     answer_generation/answer.py) — a citation pointing at a doc/page that
     wasn't actually retrieved is a hallucination signal, and DNA doc Core
     Rule #4 says a hallucinated-but-correct answer is still a failure.
"""

SYSTEM_PROMPT_TEMPLATE = """You are a financial document analyst. You answer questions ONLY from the excerpts provided below, retrieved from the user's own library of financial documents (credit reports, equity analysis, performance reports, SEC filings). You are closed-book: you have no other knowledge of these companies, and you must never use general knowledge, industry benchmarks, or anything from your training to interpret, estimate, or complete an answer — not even something you're confident is true — unless it is explicitly stated in the excerpts below.

CORE RULES (non-negotiable):
1. Answer strictly from the excerpts below. If they don't support an answer, say so — do not reason your way to a plausible-sounding answer from outside knowledge.
2. If no excerpt supports an answer, that IS the answer: status="not_found". Never guess.
3. Every claim in your answer must be traceable to a specific excerpt (document + page). If you can't cite it, don't say it.
4. Give exactly ONE answer — never present multiple competing answers for the user to sort out. The one exception is when you must ask a clarifying question first (see below) — that's not competing answers, it's deferring the answer.
5. If you find the relevant concept discussed but the specific number/figure isn't in the excerpts, that's a PARTIAL answer (status="partial"), not a full answer and not a "not found."
6. Never mention or imply a confidence score, percentage, or numeric certainty anywhere in your answer text — the user should never see a number that isn't a fact from the documents.

WHEN EXCERPTS DISAGREE (near-tie conflict):
If two or more excerpts give materially different figures for the same thing, and you can't tell which one the user should trust (e.g. no clear indication one is more authoritative, more recent, or more specific to the question), do NOT silently pick one. Set status="needs_clarification_conflict", list every conflicting source with its figure in `conflicting_sources`, and write a `clarification_question` asking the user which source to use.

WHEN A CONCEPT IS DEFINED DIFFERENTLY ACROSS DOCUMENTS:
If the question's key term or concept (e.g. "Debt-to-Income Ratio," "EBITDA," "operating margin") is defined or calculated differently across the excerpts in a way that would change the answer, do NOT guess which definition the user means. Set status="needs_clarification_concept", list every distinct definition found in `concept_definitions` with its source, and write a `clarification_question` asking the user which one they mean.

NUMERIC FORMATTING:
Round for readability at the appropriate scale (nearest thousand or million) but never over-round a precise figure — $22.1 million must stay "$22.1 million," not collapse to "$22 million." Keep the source document's precision.

TABLE FORMATTING:
If the answer is naturally a set of line items each with multiple attributes — a rollforward or activity schedule (beginning balance, additions, reductions, ending balance), a multi-period comparison, or any other data that reads as rows and columns rather than a single fact — write the `answer` field as a GFM-style Markdown pipe table (a header row, a `---` separator row, then one data row per line item) instead of a bulleted or narrative list. Put any short surrounding context (e.g. "Adobe's RSU activity for fiscal 2022 was as follows:") as a plain sentence before the table, and any figures that don't belong to a row of the table (e.g. an aggregate total, a contractual-life figure) as a plain sentence after it. Only use a table when the data genuinely has this repeated-structure shape — a single figure or a short narrative answer should stay plain prose, not be forced into a table.

ANSWER FORMAT:
When you can answer (status="answered" or "partial"), every citation must include: the document name, the page number, the exact passage or data point you drew from (`sourced_via`), why it's relevant to the question (`relevance`), and the concept/section it maps to (`concept`). Also write a `reasoning_trace` — a plain-language account of how you arrived at the answer — this is hidden from the user by default and only shown if they expand it, so it can be more detailed/technical than the main answer.

Here are the retrieved excerpts, each tagged with its document name, page number, and a reference id you must use exactly when citing:

{context_block}
"""

SUBMIT_ANSWER_TOOL = {
    "name": "submit_answer",
    "description": "Submit the final answer to the user's financial-document question, following the Standard Answer Format.",
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": [
                    "answered",
                    "partial",
                    "not_found",
                    "needs_clarification_conflict",
                    "needs_clarification_concept",
                ],
                "description": (
                    "answered: fully supported by the excerpts. partial: the concept was found "
                    "but the specific figure wasn't. not_found: nothing in the excerpts supports "
                    "an answer. needs_clarification_conflict: excerpts disagree and there's no "
                    "clear winner. needs_clarification_concept: the question's key concept is "
                    "defined differently across excerpts."
                ),
            },
            "answer": {
                "type": ["string", "null"],
                "description": "The direct answer text. Required for status=answered or partial; null otherwise.",
            },
            "partial_note": {
                "type": ["string", "null"],
                "description": "For status=partial only: what concept was found and what specific figure was missing.",
            },
            "citations": {
                "type": "array",
                "description": "One entry per excerpt actually used to support the answer. Empty for not_found/clarification statuses.",
                "items": {
                    "type": "object",
                    "properties": {
                        "doc_name": {"type": "string"},
                        "page_number": {"type": "integer"},
                        "sourced_via": {"type": "string", "description": "The exact passage or data point drawn from this excerpt."},
                        "relevance": {"type": "string", "description": "Why this excerpt is relevant to the question."},
                        "concept": {"type": "string", "description": "The concept/section this excerpt maps to."},
                    },
                    "required": ["doc_name", "page_number", "sourced_via", "relevance", "concept"],
                },
            },
            "conflicting_sources": {
                "type": ["array", "null"],
                "description": "For status=needs_clarification_conflict: every source with its conflicting figure.",
                "items": {
                    "type": "object",
                    "properties": {
                        "doc_name": {"type": "string"},
                        "page_number": {"type": "integer"},
                        "value": {"type": "string"},
                    },
                    "required": ["doc_name", "page_number", "value"],
                },
            },
            "concept_definitions": {
                "type": ["array", "null"],
                "description": "For status=needs_clarification_concept: every distinct definition found, with its source.",
                "items": {
                    "type": "object",
                    "properties": {
                        "doc_name": {"type": "string"},
                        "page_number": {"type": "integer"},
                        "definition": {"type": "string"},
                    },
                    "required": ["doc_name", "page_number", "definition"],
                },
            },
            "clarification_question": {
                "type": ["string", "null"],
                "description": "For either needs_clarification_* status: the question to ask the user.",
            },
            "reasoning_trace": {
                "type": "string",
                "description": "Plain-language account of how the answer was derived. Hidden by default, shown on expand.",
            },
        },
        "required": ["status", "answer", "citations", "reasoning_trace"],
    },
}


def build_context_block(chunks: list[dict]) -> str:
    """Renders retrieved chunks into the numbered, citable excerpt block the
    system prompt references. ref ids are stable within one call so the
    model can cite unambiguously and we can cross-check citations after."""
    parts = []
    for i, c in enumerate(chunks, start=1):
        heading = f" (section: {c['section_heading']})" if c.get("section_heading") else ""
        kind = " [TABLE]" if c.get("chunk_type") == "table" else ""
        parts.append(
            f"[ref {i}] {c['doc_name']}, page {c['page_number']}{heading}{kind}\n{c['text']}\n"
        )
    return "\n---\n".join(parts)


def build_system_prompt(chunks: list[dict]) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(context_block=build_context_block(chunks))
