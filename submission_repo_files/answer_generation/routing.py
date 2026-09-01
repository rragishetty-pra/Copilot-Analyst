"""
Haiku/Sonnet routing heuristic (DNA doc §7: "Claude Haiku 4.5 (default) with
selective routing to Claude Sonnet 5 for complex multi-document synthesis").

The DNA doc names the principle but not the rule. This is a concrete,
inspectable proposal for that rule — flagged for review, not a hidden
judgment call. Two independent signals, either one triggers Sonnet:

  1. Retrieval spans several distinct source documents. A question answerable
     from one document's chunks is rarely what the DNA doc means by "complex
     synthesis" — it's cross-document reasoning that needs the stronger model.
  2. The question's own wording asks for comparison/synthesis explicitly
     ("compare", "trend", "across", "versus", "combined", "over time", etc.)
     — even if retrieval happens to surface chunks from one document, the
     user is asking for reasoning Haiku is more likely to get subtly wrong.

Deliberately NOT based on chunk count or token count alone — a single dense
table can retrieve many chunks without needing cross-document synthesis.
"""
import re

from config import MODEL_DEFAULT, MODEL_COMPLEX

_SYNTHESIS_KEYWORDS = re.compile(
    r"\b(compare|comparison|versus|vs\.?|trend|over time|across|combined|"
    r"synthesi[sz]e|relative to|change (from|between)|year[- ]over[- ]year|"
    r"how does .* differ|which (company|companies|filing)|both|all three|"
    r"correlat)\b",
    re.IGNORECASE,
)

# Distinct-document threshold: 3+ distinct source docs among the retrieved
# chunks is a reasonable proxy for "this question needed the corpus, not a
# document" — 2 could just be an artifact of TF-IDF surfacing a tangential
# chunk from a second file.
DISTINCT_DOC_THRESHOLD = 3


def choose_model(question: str, retrieved_chunks: list[dict]) -> tuple[str, str]:
    """Returns (model_id, reason) — the reason is logged, not shown to the
    user (DNA doc §2 rule 7: no confidence/internal-signal exposure)."""
    distinct_docs = {c["doc_id"] for c in retrieved_chunks}

    if len(distinct_docs) >= DISTINCT_DOC_THRESHOLD:
        return MODEL_COMPLEX, (
            f"retrieval spans {len(distinct_docs)} distinct documents "
            f"(>= {DISTINCT_DOC_THRESHOLD})"
        )

    if _SYNTHESIS_KEYWORDS.search(question):
        return MODEL_COMPLEX, "question wording signals cross-document synthesis"

    return MODEL_DEFAULT, "single-document / direct-lookup question"
