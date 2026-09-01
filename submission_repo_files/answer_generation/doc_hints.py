"""
Document-name-aware retrieval boost — a fix for a real retrieval-quality gap
found during testing (not a hypothetical): pure TF-IDF cosine similarity lets
generic, densely-repeated financial phrases ("total revenue," "net income")
in a small table chunk dominate a query, even when the query also names a
specific company. A query like "Costco 2021 10-K total revenue" retrieved
Best Buy and Adobe chunks with NO Costco chunks in the top 10, because those
chunks repeat "total revenue" many times in a short table while the actual
Costco chunk's mention of it is diluted across a page of prose. This is a
structural property of TF-IDF, not something sublinear_tf alone fixes.

The corpus's filenames are consistently `COMPANY_YEAR_TYPE.htm` (e.g.
COSTCO_2021_10K.htm, JOHNSON_JOHNSON_2022_10K.htm) — that's a free, reliable
signal this module exploits: detect company names mentioned in the question,
and if any match a document in the corpus, retrieve preferentially from that
document rather than trusting global cosine similarity alone to find it.

This is a heuristic, not a real entity-linking system — documented
limitations: short/common company names (e.g. "AES", 3 letters) can
false-positive on unrelated substrings, and it only fires when the question's
wording is close enough to the filename to survive punctuation-stripping.
Worth replacing with real NER or a company alias table if this corpus grows.
"""
import re
from functools import lru_cache

from vectorstore.local_store import _get_conn

_SUFFIX_RE = re.compile(
    r"_(\d{4}(Q\d)?)_?(10K|10Q|8K)?(_dated.*)?$", re.IGNORECASE
)
_MIN_KEY_LENGTH = 4  # guards against short/common tokens like "AES" false-positiving broadly


def _squash(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


_YEAR_RE = re.compile(r"(19|20)\d{2}")


@lru_cache(maxsize=1)
def _company_index() -> list[tuple[str, str, str, str | None]]:
    """Returns [(squashed_company_key, doc_id, doc_name, year), ...] derived
    from every distinct document currently in the store. Cached — call
    doc_hints.refresh() after ingesting new documents."""
    with _get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT doc_id, doc_name FROM chunks").fetchall()

    index = []
    for row in rows:
        stem = row["doc_name"].rsplit(".", 1)[0]  # drop .htm/.pdf
        year_match = _YEAR_RE.search(stem)
        year = year_match.group(0) if year_match else None
        key = _SUFFIX_RE.sub("", stem)              # drop _2021_10K / _2022_8K_dated-... suffix
        squashed = _squash(key)
        if len(squashed) >= _MIN_KEY_LENGTH:
            index.append((squashed, row["doc_id"], row["doc_name"], year))
    return index


def refresh() -> None:
    """Call after ingesting new documents so newly added companies are
    detectable without restarting the process."""
    _company_index.cache_clear()


def detect_mentioned_doc_ids(question: str) -> list[str]:
    """Returns doc_ids whose company key appears in the question, longest
    key first (so e.g. 'JOHNSON_JOHNSON' matches before a shorter
    coincidental substring would). Empty list means no confident match —
    callers should fall back to plain corpus-wide retrieval.

    When a company has multiple filings in the corpus (e.g. Nike 2018/2019/
    2021/2023) and the question also names a year, narrows to just the
    matching year's document(s) — without this, "Nike revenue for fiscal
    2023" could still retrieve the 2019 filing instead if the newer one's
    chunks happen to score lower on cosine similarity for unrelated reasons."""
    squashed_question = _squash(question)
    mentioned_years = {m.group(0) for m in _YEAR_RE.finditer(question)}

    matches = [
        (key, doc_id, year) for key, doc_id, _, year in _company_index()
        if key in squashed_question
    ]
    matches.sort(key=lambda kv: len(kv[0]), reverse=True)

    seen = set()
    doc_ids = []
    for _, doc_id, _year in matches:
        if doc_id not in seen:
            seen.add(doc_id)
            doc_ids.append(doc_id)

    if mentioned_years and len(doc_ids) > 1:
        by_id = {doc_id: year for _, doc_id, year in matches}
        year_filtered = [d for d in doc_ids if by_id.get(d) in mentioned_years]
        if year_filtered:
            return year_filtered

    return doc_ids
