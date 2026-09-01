"""
Central configuration for the ingestion pipeline.
Kept as one small, readable file rather than scattering constants across modules —
these are exactly the knobs the DNA doc's design decisions map to.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent
RAW_DOCS_DIR = BASE_DIR / "data" / "raw"          # encrypted originals live here
PROCESSED_DIR = BASE_DIR / "data" / "processed"   # extracted text/table cache (JSON)
INCOMING_DIR = BASE_DIR / "data" / "incoming"      # staging area for files downloaded from Drive
STORE_DIR = BASE_DIR / "data" / "vector_store"     # persistent local vector index
LOGS_DIR = BASE_DIR / "logs"

for d in (RAW_DOCS_DIR, PROCESSED_DIR, INCOMING_DIR, STORE_DIR, LOGS_DIR):
    d.mkdir(parents=True, exist_ok=True)

SQLITE_DB_PATH = STORE_DIR / "chunks.db"
TFIDF_MATRIX_PATH = STORE_DIR / "tfidf_matrix.npz"
VECTORIZER_PATH = STORE_DIR / "vectorizer.pkl"

# --- Chunking (DNA doc §4, §5: citations must resolve to a specific page) ---
# Chunks never span a page boundary — every chunk maps to exactly one page,
# which is what makes "document + page number" citation possible at all.
# Sizes are in approximate words, not true LLM tokens (tiktoken isn't
# installable in this sandbox — see embeddings/embedder.py for why that's an
# acceptable simplification for a *sizing* heuristic).
CHUNK_TARGET_WORDS = 350
CHUNK_OVERLAP_WORDS = 50
MIN_CHUNK_WORDS = 25  # smaller trailing fragments get merged into the previous chunk

# --- Embeddings ---
# TF-IDF + cosine similarity, fit locally over the corpus (see embeddings/embedder.py).
TFIDF_MAX_FEATURES = 50000
TFIDF_NGRAM_RANGE = (1, 2)  # unigrams + bigrams — bigrams help match phrases like "debt to income"

# --- Vector store ---
# No fixed document/character ceiling like claude.ai Project knowledge — the
# practical limits are disk and RAM, both far beyond a 78-doc/400MB corpus.
DEFAULT_TOP_K = 8

# --- Encryption at rest (DNA doc §8) ---
DOC_ENCRYPTION_KEY = os.environ.get("DOC_ENCRYPTION_KEY", "")

# --- Access logging (DNA doc §8: mandatory, not optional) ---
ACCESS_LOG_PATH = LOGS_DIR / "access.log.jsonl"

# --- Answer generation (DNA doc §7, §2, §4, §5) ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_BASE = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"

# Model routing: Haiku is the default (cheap, fast); Sonnet is reserved for
# genuinely complex multi-document synthesis (see answer_generation/routing.py
# for the concrete heuristic — this is a judgment call flagged for review,
# not something the DNA doc specifies precisely).
MODEL_DEFAULT = "claude-haiku-4-5"
MODEL_COMPLEX = "claude-sonnet-4-5"

# Retrieval -> answer-generation signals. DNA doc §2 rule 7: never shown to
# the user as a number — internal only.
#
# NOT_FOUND_SCORE_THRESHOLD was removed as a hard "skip the LLM" gate after
# testing showed raw TF-IDF cosine scores aren't a reliable relevance signal
# on their own (a correct answer scored 0.13; "what is the capital of
# France" scored 0.894 against an unrelated chunk dominated by the common
# term "capital"). The LLM's own closed-book judgment is the not-found
# authority now (see answer_generation/answer.py) — only a genuinely empty
# retrieval result short-circuits before the LLM call.
CONFLICT_SCORE_GAP_THRESHOLD = 0.05  # top-2 chunks disagree on a figure AND their
                                       # scores are within this gap -> near-tie, ask the user
                                       # rather than silently pick (per user's explicit decision)

# Conversational memory (DNA doc §6): follow-ups use prior context within
# this window of inactivity; after it elapses the next question starts fresh.
SESSION_IDLE_WINDOW_SECONDS = 180
SESSIONS_DB_PATH = STORE_DIR / "sessions.db"

# --- Document source: Google Drive folder ---
# This is the folder the user drops finance documents into. The pipeline
# treats it as the corpus's source of truth for uploads (DNA doc §6: the
# "documents panel" side of upload — Drive IS that panel here, rather than a
# separate one built into an app UI that doesn't exist yet).
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "1jmMfmfjmoalc-Jh8boJp3WIilSR6_RJ6")
DRIVE_FOLDER_NAME = "Copilot Analyst"
DRIVE_SYNC_STATE_PATH = STORE_DIR / "drive_sync_state.json"
