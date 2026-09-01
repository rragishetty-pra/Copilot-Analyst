"""
Persistent local vector store — no server, no subscription, no fixed
document/character ceiling (unlike claude.ai Project knowledge). The DNA doc
(§7) names Chroma or FAISS for this; neither is installable in this sandbox
(pip installs are blocked for anything not already cached — see
embeddings/embedder.py for the full explanation). This module fills the same
role with two pieces that are already available anywhere Python runs:

  - SQLite (stdlib) holds chunk text + citation metadata: doc_id, doc_name,
    page_number, section_heading, chunk_type. This is the source of truth.
  - A scipy sparse matrix holds the TF-IDF vectors, persisted to disk and
    row-aligned with the SQLite rows via a `row_index` column.

Retrieval is brute-force cosine similarity over the sparse matrix. At this
project's scale (78 docs, tens of thousands of chunks) that's milliseconds —
an ANN index (which is what Chroma/FAISS add on top) only starts to matter at
a much larger scale than this corpus will ever reach.

The public functions here (doc_already_ingested, add_chunks, rebuild_index,
query, collection_stats, delete_document) are the seam: swapping in real
Chroma+BGE later means rewriting this file's internals, not any caller.
"""
import logging
import re
import sqlite3

import numpy as np
from scipy import sparse
from sklearn.metrics.pairwise import cosine_similarity

from config import SQLITE_DB_PATH, TFIDF_MATRIX_PATH, DEFAULT_TOP_K
from ingestion.models import Chunk
from embeddings import embedder

logger = logging.getLogger(__name__)

# --- Company-name detection / boost -----------------------------------------
# TF-IDF cosine similarity has no notion of "this chunk is about the company
# the question names" — it just measures term overlap. That means a long,
# multi-topic chunk (e.g. a filing's full "executive officers" bio section)
# can score LOWER than a short, unrelated chunk that happens to repeat the
# query's generic terms ("chief", "executive", "officer", a stray date), even
# though the long chunk contains the literal answer. Confirmed during
# testing: "who is the CEO of Adobe" — the correct chunk verbatim named
# Shantanu Narayen as Adobe's CEO, but ranked ~46th out of 38,821 chunks,
# well outside DEFAULT_TOP_K, because it's a dense bio section diluted by
# unrelated text about other officers.
#
# Rather than a full embeddings upgrade (the DNA doc's long-term fallback,
# Sec 12.5), this closes the gap cheaply for the common case of a
# company-named question: detect which company (if any) the query names,
# using this corpus's own doc_name convention (COMPANY_YEAR_FORM.htm), and
# give that company's chunks a fixed score boost so they're never crowded
# out by coincidental term overlap from unrelated filings. Only applied when
# exactly one company is detected, to avoid guessing on ambiguous questions.
_COMPANY_BOOST = 1.0

_company_prefix_cache: dict[int, str] | None = None
_all_prefixes_cache: list[str] | None = None


def _company_prefix(doc_name: str) -> str:
    """Normalize a doc_name (e.g. 'ADOBE_2022_10K.htm', 'JOHNSON_JOHNSON_2022_10K.htm')
    down to a bare, comparable company key ('adobe', 'johnsonjohnson')."""
    match = re.match(r"^([A-Za-z_]+?)_\d{4}", doc_name)
    raw = match.group(1) if match else doc_name.split("_")[0]
    return re.sub(r"[^a-z0-9]", "", raw.lower())


def _load_company_prefix_index(conn: sqlite3.Connection) -> tuple[dict[int, str], list[str]]:
    """Build (and cache for this process) a row_index -> company-prefix map
    plus the sorted list of distinct prefixes present in the corpus. Cheap
    at this corpus's scale (tens of thousands of rows), but no need to repeat
    it on every single query."""
    global _company_prefix_cache, _all_prefixes_cache
    if _company_prefix_cache is not None and _all_prefixes_cache is not None:
        return _company_prefix_cache, _all_prefixes_cache

    rows = conn.execute(
        "SELECT row_index, doc_name FROM chunks WHERE row_index IS NOT NULL"
    ).fetchall()
    prefix_by_row = {r["row_index"]: _company_prefix(r["doc_name"]) for r in rows}
    all_prefixes = sorted(set(prefix_by_row.values()), key=len, reverse=True)

    _company_prefix_cache, _all_prefixes_cache = prefix_by_row, all_prefixes
    return prefix_by_row, all_prefixes


def _detect_company(query_text: str, all_prefixes: list[str]) -> list[str]:
    """Best-effort: which corpus company (if any) does this query name?
    Matches on a punctuation/space-stripped query so multi-word names like
    "Best Buy" or "Johnson & Johnson" line up with their doc_name prefixes
    ("bestbuy", "johnsonjohnson") without a hand-maintained alias table.
    Returns every prefix that matches; callers should only act on this when
    it resolves to exactly one, to avoid boosting the wrong company on a
    genuinely ambiguous or multi-company question."""
    normalized_query = re.sub(r"[^a-z0-9]", "", query_text.lower())
    return [p for p in all_prefixes if len(p) >= 2 and p in normalized_query]

# --- Exact-date detection / boost -------------------------------------------
# TF-IDF cosine similarity treats a query like "the balance at December 2,
# 2022" as just more terms competing with "balance," "stockholders equity,"
# "retained earnings" — extremely common phrases repeated near-identically
# across every filing's financial statements. Confirmed during testing: a
# query naming a specific, rare date but no company name returned ZERO chunks
# from the one document that actually contains that date (Adobe's FY2022
# equity statement, "Balances at December 2, 2022") anywhere in the top 8 —
# generic statement boilerplate from six unrelated companies (General Mills,
# Microsoft, Amazon, Block, MGM, Best Buy) crowded it out entirely, because a
# single rare date mention gets diluted to nothing inside a dense financial
# table when TF-IDF has dozens of other numbers/terms to weigh it against.
#
# Fix: detect date-shaped substrings in the query text and directly search
# for literal (comma-normalized) matches in the stored chunk text — an exact
# date match is a strong, low-false-positive signal that cosine similarity
# structurally can't give enough weight to on its own. Same pattern as the
# company-name boost above, just keyed on dates instead of doc_name prefixes.
_DATE_BOOST = 2.0
_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\.?\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b",
    re.IGNORECASE,
)


def _detect_date_mentions(query_text: str) -> list[str]:
    """Returns normalized 'Month D, YYYY' strings for every date-shaped
    mention in the query, in the exact comma'd form SEC filings consistently
    use for statement dates — so a query written without the comma ("December
    2 2022") still matches text that has one ("December 2, 2022")."""
    mentions = []
    for m in _DATE_RE.finditer(query_text):
        month, day, year = m.group(1), m.group(2), m.group(3)
        mentions.append(f"{month.capitalize()} {int(day)}, {year}")
    return mentions


def _rows_mentioning_dates(conn: sqlite3.Connection, dates: list[str]) -> set[int]:
    """Row indices of chunks whose text literally contains one of these exact
    dates. A direct LIKE scan rather than a persistent index — this only runs
    when the query actually contains a date-shaped phrase, which is rare, so
    the cost is fine at this corpus's scale (tens of thousands of chunks)."""
    rows: set[int] = set()
    for date_str in dates:
        for r in conn.execute(
            "SELECT row_index FROM chunks WHERE row_index IS NOT NULL AND text LIKE ?",
            (f"%{date_str}%",),
        ).fetchall():
            rows.add(r["row_index"])
    return rows


_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    doc_name TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    section_heading TEXT,
    chunk_type TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    token_count INTEGER NOT NULL,
    text TEXT NOT NULL,
    row_index INTEGER
);
CREATE INDEX IF NOT EXISTS idx_doc_id ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_row_index ON chunks(row_index);
"""


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(SQLITE_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def doc_already_ingested(doc_id: str) -> bool:
    """Content-hash based idempotency check (DNA doc §3: re-ingesting an
    unchanged file should not create duplicate chunks)."""
    with _get_conn() as conn:
        row = conn.execute("SELECT 1 FROM chunks WHERE doc_id = ? LIMIT 1", (doc_id,)).fetchone()
        return row is not None


def add_chunks(chunks: list[Chunk]) -> None:
    """Write chunk text + metadata to SQLite. Does NOT update the search
    index — call rebuild_index() afterward (the pipeline does this once per
    ingestion run, not once per chunk, since TF-IDF needs the full corpus)."""
    if not chunks:
        return
    with _get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO chunks
               (chunk_id, doc_id, doc_name, page_number, section_heading,
                chunk_type, chunk_index, token_count, text, row_index)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            [
                (c.chunk_id, c.doc_id, c.doc_name, c.page_number, c.section_heading,
                 c.chunk_type, c.chunk_index, c.token_count, c.text)
                for c in chunks
            ],
        )
    logger.info("Wrote %d chunks to SQLite (index rebuild pending)", len(chunks))


def rebuild_index() -> int:
    """Refit the TF-IDF vectorizer over every chunk currently in the store and
    rewrite the vector matrix. Returns the number of chunks indexed."""
    # New/removed documents can change row_index assignments and the set of
    # companies in the corpus — drop the cached company-prefix index so the
    # next query() call rebuilds it from the fresh data instead of serving a
    # stale row_index -> company mapping.
    global _company_prefix_cache, _all_prefixes_cache
    _company_prefix_cache, _all_prefixes_cache = None, None

    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT chunk_id, text FROM chunks ORDER BY rowid"
        ).fetchall()

        if not rows:
            logger.warning("rebuild_index called with no chunks in the store.")
            return 0

        chunk_ids = [r["chunk_id"] for r in rows]
        texts = [r["text"] for r in rows]

        vectorizer, matrix = embedder.fit_corpus(texts)
        embedder.save_vectorizer(vectorizer)
        sparse.save_npz(str(TFIDF_MATRIX_PATH), matrix)

        conn.executemany(
            "UPDATE chunks SET row_index = ? WHERE chunk_id = ?",
            [(i, cid) for i, cid in enumerate(chunk_ids)],
        )

    logger.info("Rebuilt vector index: %d chunks", len(rows))
    return len(rows)


def query(query_text: str, top_k: int = DEFAULT_TOP_K, doc_id: str | None = None) -> list[dict]:
    """Return top_k chunks ranked by cosine similarity, each with full
    citation metadata attached — the shape the answer-generation layer
    (Claude API) will consume directly."""
    vectorizer = embedder.load_vectorizer()
    if vectorizer is None or not TFIDF_MATRIX_PATH.exists():
        logger.warning("No index built yet — ingest documents first.")
        return []

    matrix = sparse.load_npz(str(TFIDF_MATRIX_PATH))
    query_vec = embedder.embed_query(vectorizer, query_text)

    sims = cosine_similarity(query_vec, matrix)[0]

    with _get_conn() as conn:
        # Company-name boost (see _detect_company above) — skipped entirely
        # when the caller already scoped the search to one document via
        # doc_id, since that's a stronger, explicit signal than a guess from
        # the query text.
        if not doc_id:
            prefix_by_row, all_prefixes = _load_company_prefix_index(conn)
            detected = _detect_company(query_text, all_prefixes)
            if len(detected) == 1:
                company = detected[0]
                boost_mask = np.fromiter(
                    (prefix_by_row.get(i) == company for i in range(len(sims))),
                    dtype=bool, count=len(sims),
                )
                sims = sims + boost_mask * _COMPANY_BOOST
                logger.debug("Query names %r — boosted %d chunks", company, int(boost_mask.sum()))

            date_mentions = _detect_date_mentions(query_text)
            if date_mentions:
                date_rows = _rows_mentioning_dates(conn, date_mentions)
                if date_rows:
                    date_boost_mask = np.fromiter(
                        (i in date_rows for i in range(len(sims))),
                        dtype=bool, count=len(sims),
                    )
                    sims = sims + date_boost_mask * _DATE_BOOST
                    logger.debug("Query names dates %r — boosted %d chunks", date_mentions, int(date_boost_mask.sum()))

        if doc_id:
            allowed_rows = {
                r["row_index"] for r in conn.execute(
                    "SELECT row_index FROM chunks WHERE doc_id = ? AND row_index IS NOT NULL",
                    (doc_id,),
                ).fetchall()
            }
            for i in range(len(sims)):
                if i not in allowed_rows:
                    sims[i] = -1.0

        top_indices = np.argsort(sims)[::-1][:top_k]
        top_indices = [int(i) for i in top_indices if sims[i] > 0]

        hits = []
        for row_index in top_indices:
            row = conn.execute(
                "SELECT * FROM chunks WHERE row_index = ?", (row_index,)
            ).fetchone()
            if row is None:
                continue
            hits.append({
                "chunk_id": row["chunk_id"],
                "text": row["text"],
                "score": float(sims[row_index]),
                "doc_id": row["doc_id"],
                "doc_name": row["doc_name"],
                "page_number": row["page_number"],
                "section_heading": row["section_heading"],
                "chunk_type": row["chunk_type"],
            })
        return hits


def list_documents() -> list[dict]:
    """Every distinct document currently in the store, for the app's
    documents panel (DNA doc §6)."""
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT doc_id, doc_name, COUNT(*) as chunk_count,
                      MAX(page_number) as page_count
               FROM chunks GROUP BY doc_id, doc_name ORDER BY doc_name"""
        ).fetchall()
    return [dict(r) for r in rows]


def collection_stats() -> dict:
    with _get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        docs = conn.execute("SELECT COUNT(DISTINCT doc_id) FROM chunks").fetchone()[0]
    return {"total_chunks": total, "distinct_documents": docs}


def delete_document(doc_id: str) -> int:
    """Remove a document's chunks. Caller must run rebuild_index() afterward
    to refresh the search index (kept explicit rather than automatic, since
    rebuilding is the expensive step and callers may want to batch deletes)."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        return cur.rowcount
