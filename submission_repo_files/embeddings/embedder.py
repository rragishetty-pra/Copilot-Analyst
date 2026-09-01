"""
Embeddings backend.

The DNA doc (§7) specifies BGE via sentence-transformers: a self-hosted dense
embedding model, chosen for zero ongoing cost. That's still the right choice
for this project once it's running somewhere with normal internet access.
In *this* sandboxed session, though, pip installs are blocked outright — both
PyPI and npm return 403 for any package not already cached, which rules out
torch/sentence-transformers/chromadb/faiss/tiktoken entirely. That's a
property of this cloud sandbox, not a limitation of the design.

So this module implements the same embedding *interface* (fit once, embed
passages, embed queries) using only what's already installed here:
scikit-learn's TF-IDF vectorizer, used directly (sparse vectors) rather than
reduced with something like TruncatedSVD — for financial filings this is
arguably not even a downgrade for the questions this project cares about
most: TF-IDF preserves exact tokens, so a query for "$22.1 million" or
"Item 7A" matches on the literal figure/heading rather than getting smoothed
away the way dense semantic embeddings sometimes do. Production RAG systems
often combine both (dense + lexical/BM25) for exactly this reason.

Swapping in real BGE embeddings later is a contained change: everything that
calls into this module goes through fit_corpus() / embed_query() only, never
touches sklearn directly.
"""
import difflib
import logging
import pickle
import re
import sqlite3

from scipy import sparse
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer

from config import SQLITE_DB_PATH, TFIDF_MAX_FEATURES, TFIDF_NGRAM_RANGE, VECTORIZER_PATH

logger = logging.getLogger(__name__)

# --- Query-side abbreviation/synonym expansion ------------------------------
# TF-IDF only matches literal tokens: a query using an abbreviation ("CEO")
# won't match indexed text that spells the term out ("Chief Executive
# Officer"), and vice versa. Confirmed gap: "who is the CEO of Adobe" missed
# a chunk that verbatim says "Shantanu Narayen ... Chief Executive Officer"
# because the two share no tokens. This is the recall-gap risk flagged in the
# DNA doc (Sec 12.5) for the free/local TF-IDF approach vs. real semantic
# embeddings. Rather than a full embeddings upgrade, we close the common
# finance/business cases cheaply: before vectorizing a query, append the
# expansion (or abbreviation) for any recognized term so both forms are
# present as tokens and either one can match the corpus's actual phrasing.
_ABBREVIATIONS: dict[str, str] = {
    "ceo": "chief executive officer",
    "cfo": "chief financial officer",
    "coo": "chief operating officer",
    "cto": "chief technology officer",
    "cio": "chief information officer",
    "cmo": "chief marketing officer",
    "chro": "chief human resources officer",
    "gc": "general counsel",
    "eps": "earnings per share",
    "roi": "return on investment",
    "roe": "return on equity",
    "roa": "return on assets",
    "yoy": "year over year",
    "qoq": "quarter over quarter",
    "fy": "fiscal year",
    "gaap": "generally accepted accounting principles",
    "dti": "debt to income",
    "m&a": "mergers and acquisitions",
    "r&d": "research and development",
    "cogs": "cost of goods sold",
    "ebitda": "earnings before interest taxes depreciation and amortization",
    "cagr": "compound annual growth rate",
    "ipo": "initial public offering",
    "sec": "securities and exchange commission",
    "capex": "capital expenditures",
    "opex": "operating expenses",
}


# --- Query-side spelling correction ----------------------------------------
# Same root problem as abbreviation expansion, different cause: TF-IDF only
# matches literal tokens, so a misspelled word ("revenuw", "const") is
# simply invisible to it — contributes nothing to the query vector, which
# can silently gut a question down to just its stopwords. Confirmed gap:
# "what is the const of revenuw" retrieved nothing usable because neither
# "const" nor "revenuw" exist anywhere in the corpus's vocabulary.
#
# This corrects against the corpus's OWN vocabulary (not a general English
# dictionary) — company names and financial terms actually used in these
# filings are exactly what's worth correcting toward, and a general
# dictionary wouldn't know "EBITDA" or "Kraft Heinz" are valid. Company
# names get their own, slightly more permissive matching pass first (a
# right/wrong company match matters more than a generic word does — a wrong
# company would wrongly scope retrieval to the wrong filings via
# doc_hints.py) with a cutoff tuned so it catches a genuine misspelling
# ("nkie" -> "nike") without also firing on a coincidental resemblance to an
# unrelated company name ("const" happens to resemble "costco" at a lower
# similarity than it resembles the correct word "cost", so the general pool
# below wins that one instead).
#
# Same additive pattern as _expand_query: the misspelled original is left
# in place (harmless — TF-IDF ignores it) and only a plausible correction
# is appended, so a wrong guess costs nothing beyond one extra token.
_WORD_RE = re.compile(r"[A-Za-z]+")
_MIN_CORRECTABLE_LENGTH = 4  # shorter words are too ambiguous to correct confidently
_COMPANY_MATCH_CUTOFF = 0.75
_VOCAB_MATCH_CUTOFF = 0.8

_company_names_cache: list[str] | None = None
_unigram_vocab_cache: tuple[int, list[str]] | None = None


def _company_names() -> list[str]:
    """Company names derived from the corpus's own doc_name convention
    (COMPANY_YEAR_FORM.htm), space-separated for readability/matching —
    cached for the life of the process since the corpus rarely changes."""
    global _company_names_cache
    if _company_names_cache is not None:
        return _company_names_cache
    names: set[str] = set()
    try:
        with sqlite3.connect(str(SQLITE_DB_PATH)) as conn:
            rows = conn.execute("SELECT DISTINCT doc_name FROM chunks").fetchall()
        for (doc_name,) in rows:
            match = re.match(r"^([A-Za-z_]+?)_\d{4}", doc_name)
            raw = match.group(1) if match else doc_name.split("_")[0]
            names.add(raw.replace("_", " ").lower())
    except sqlite3.Error:
        logger.warning("Could not load company names for spell correction — skipping that pass.")
    _company_names_cache = sorted(names)
    return _company_names_cache


def _unigram_vocabulary(vectorizer: TfidfVectorizer) -> list[str]:
    """Single-word vocabulary entries only (the fitted vocabulary also
    contains bigrams, which aren't meaningful spell-correction targets for
    one misspelled word at a time). Cached per vectorizer instance."""
    global _unigram_vocab_cache
    if _unigram_vocab_cache is not None and _unigram_vocab_cache[0] == id(vectorizer):
        return _unigram_vocab_cache[1]
    unigrams = [w for w in vectorizer.vocabulary_ if " " not in w]
    _unigram_vocab_cache = (id(vectorizer), unigrams)
    return unigrams


def _correct_spelling(vectorizer: TfidfVectorizer, query: str) -> str:
    vocab = vectorizer.vocabulary_
    corrections = []
    for word in _WORD_RE.findall(query):
        lowered = word.lower()
        if len(lowered) < _MIN_CORRECTABLE_LENGTH or lowered in vocab or lowered in ENGLISH_STOP_WORDS:
            continue  # already a real match, a stopword, or too short to correct confidently
        company_hit = difflib.get_close_matches(lowered, _company_names(), n=1, cutoff=_COMPANY_MATCH_CUTOFF)
        if company_hit:
            corrections.append(company_hit[0])
            continue
        vocab_hit = difflib.get_close_matches(lowered, _unigram_vocabulary(vectorizer), n=1, cutoff=_VOCAB_MATCH_CUTOFF)
        if vocab_hit:
            corrections.append(vocab_hit[0])
    if corrections:
        corrected = query + " " + " ".join(corrections)
        logger.debug("Spell-corrected query %r -> %r", query, corrected)
        return corrected
    return query


def _expand_query(query: str) -> str:
    """Append any recognized abbreviation<->expansion pairs found in the
    query so TF-IDF can match whichever form the source document actually
    uses. Additive only (never rewrites the original query) so the user's
    exact wording is still matched too."""
    lowered = query.lower()
    additions = []
    for abbrev, expansion in _ABBREVIATIONS.items():
        if re.search(r"\b" + re.escape(abbrev) + r"\b", lowered):
            additions.append(expansion)
        elif re.search(r"\b" + re.escape(expansion) + r"\b", lowered):
            additions.append(abbrev)
    if additions:
        expanded = query + " " + " ".join(additions)
        logger.debug("Expanded query %r -> %r", query, expanded)
        return expanded
    return query


def fit_corpus(texts: list[str]) -> tuple[TfidfVectorizer, sparse.csr_matrix]:
    """Fit a fresh vectorizer over the full current corpus and transform every
    chunk. TF-IDF's vocabulary is corpus-dependent, so this is called with ALL
    chunk texts (existing + newly ingested) any time the index is rebuilt —
    the vector store owns when that happens. At this project's scale
    (78 docs / 400MB) refitting over the whole corpus on every ingest is a
    sub-second-to-low-seconds operation, not a bottleneck."""
    vectorizer = TfidfVectorizer(
        max_features=TFIDF_MAX_FEATURES,
        ngram_range=TFIDF_NGRAM_RANGE,
        stop_words="english",
        sublinear_tf=True,  # dampens raw term-frequency spikes in dense financial tables
    )
    matrix = vectorizer.fit_transform(texts)
    logger.info("Fit TF-IDF vectorizer: %d chunks, vocabulary size %d",
                len(texts), len(vectorizer.vocabulary_))
    return vectorizer, matrix


def save_vectorizer(vectorizer: TfidfVectorizer) -> None:
    with open(VECTORIZER_PATH, "wb") as f:
        pickle.dump(vectorizer, f)


def load_vectorizer() -> TfidfVectorizer | None:
    if not VECTORIZER_PATH.exists():
        return None
    with open(VECTORIZER_PATH, "rb") as f:
        return pickle.load(f)


def embed_query(vectorizer: TfidfVectorizer, query: str) -> sparse.csr_matrix:
    """Transform a user question into the same vector space as the indexed
    chunks. Must use the vectorizer that was fit on the corpus — a vectorizer
    fit on different text produces vectors that aren't comparable.

    The query is spell-corrected and expanded with recognized
    abbreviations/synonyms first (see _correct_spelling / _expand_query) —
    this only affects the query side, so no re-indexing or vectorizer
    refit is required for either fix to take effect."""
    corrected_query = _correct_spelling(vectorizer, query)
    expanded_query = _expand_query(corrected_query)
    return vectorizer.transform([expanded_query])


def correct_query_spelling(query: str) -> str:
    """Public entry point for callers that need spelling-corrected text
    BEFORE it reaches embed_query — specifically, doc_hints.py's company-
    name detection (see answer_generation/answer.py's _retrieve()) runs on
    raw question text, so a misspelled company name ("nkie") needs
    correcting at that earlier point too, not just inside embed_query,
    or the doc-scoped-retrieval boost never fires for a misspelled name.
    Loads the persisted vectorizer itself so callers outside this module
    don't need to hold one; returns the query unchanged if no vectorizer
    has been fit yet."""
    vectorizer = load_vectorizer()
    if vectorizer is None:
        return query
    return _correct_spelling(vectorizer, query)
