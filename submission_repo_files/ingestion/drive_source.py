"""
Google Drive as a document source — NOT currently used for binary PDF
transfer. Read this before re-enabling it.

Original plan: the "Copilot Analyst" Drive folder would be where finance
documents get uploaded, with Claude listing/downloading PDFs via the Drive
MCP tools (search_files / download_file_content) and staging them into
data/incoming/ for the existing pipeline to ingest.

That was tested (2026-08-17) and rejected: download_file_content returns file
bytes as a base64 string inside the tool result, and getting that content
from the tool result into a written file requires an LLM (Claude) to
reproduce that string — there is no tool-to-tool pipe that moves it as raw
bytes. For a 4.9KB synthetic test PDF, that reproduction step silently
flipped a single character inside a compressed content stream. The resulting
file was still a structurally valid, openable PDF — but pdfplumber extracted
garbled text on the affected page ("Total revenueOperating expenses .and And
modestoy $22.ovel" instead of the real sentence). No error, no warning
surfaced to the pipeline — just wrong data with a page citation attached to
it, which is the exact failure mode this whole project exists to prevent.
Real SEC filings run 70,000-150,000+ words; the odds of at least one
transcription slip only go up with size, not down.

Decided instead: PDFs reach this pipeline by being dragged directly into the
Claude conversation, which is a native platform file transfer (not a
text-relay through the model) and has no equivalent failure mode. That
matches DNA doc §6's upload requirement directly and needs no extra code —
see scripts/ingest.py and the README for the exact flow.

This module (and the Drive MCP connector) still have a legitimate use:
reading and listing TEXT content (a README, a JSONL golden-test file) via
read_file_content, where the returned string is directly usable rather than
needing byte-exact binary reconstruction. It is kept here, unused by the
ingest pipeline, in case a later phase adds a properly engineered Drive sync
(google-api-python-client + OAuth, running as real code with no LLM in the
byte path — see the note at the bottom of this file for what that would take).
"""
import json
import logging
from pathlib import Path

from config import DRIVE_SYNC_STATE_PATH, INCOMING_DIR

logger = logging.getLogger(__name__)


def load_sync_state() -> dict:
    """Returns {drive_file_id: {"name", "modifiedTime", "local_path"}}."""
    if not DRIVE_SYNC_STATE_PATH.exists():
        return {}
    with open(DRIVE_SYNC_STATE_PATH, "r") as f:
        return json.load(f)


def save_sync_state(state: dict) -> None:
    with open(DRIVE_SYNC_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def needs_download(file_id: str, modified_time: str, state: dict) -> bool:
    """True if this Drive file is new, or has changed since the last sync
    (compared by Drive's modifiedTime — cheaper than hashing content we
    haven't downloaded yet)."""
    entry = state.get(file_id)
    if entry is None:
        return True
    return entry.get("modifiedTime") != modified_time


def local_path_for(file_id: str, name: str) -> Path:
    """Deterministic local staging path for a given Drive file — same file_id
    always lands at the same path, so re-syncing an unchanged file is a
    harmless overwrite rather than an accumulating duplicate."""
    safe_name = name if name.lower().endswith(".pdf") else f"{name}.pdf"
    return INCOMING_DIR / f"{file_id}__{safe_name}"


def record_synced_file(file_id: str, name: str, modified_time: str, local_path: str, state: dict) -> dict:
    state[file_id] = {"name": name, "modifiedTime": modified_time, "local_path": str(local_path)}
    return state


# --- Note: what a properly engineered Drive sync would require ---
# 1. google-api-python-client + google-auth-oauthlib, installed where pip has
#    real network access (not this sandbox — see embeddings/embedder.py).
# 2. OAuth credentials (a Google Cloud project + consent screen, or a service
#    account with the folder shared to it) that the standalone app can hold
#    on to across runs — separate from whatever authorizes the Drive MCP
#    connector inside a Claude session.
# 3. A real Python download (Drive API's files().get_media(), streamed
#    straight to disk) — bytes never pass through an LLM's output at all,
#    which is what actually fixes the failure mode described above.
# This is a well-understood, standard integration — just out of scope for
# what's achievable inside this sandboxed session today.
