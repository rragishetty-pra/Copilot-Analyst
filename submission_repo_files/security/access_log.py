"""
Access logging (DNA doc §8: "mandatory, not optional. The system must always
log which user used it, what questions were asked, and what information was
retrieved.")

Kept deliberately simple for a single-user prototype: one append-only JSONL
file, one line per event. This is the seam where multi-user attribution
(§12, deferred) would plug in later — the `user` field already exists so
that extension doesn't require a schema change.
"""
import json
import logging
from datetime import datetime, timezone

from config import ACCESS_LOG_PATH

logger = logging.getLogger(__name__)


def log_event(action: str, user: str = "single_user", **details) -> None:
    """action examples: 'ingest', 'query', 'drive_sync', 'document_view'.
    details: free-form key/values relevant to the action (doc_name, doc_id,
    question text, retrieved chunk_ids, etc.)."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user": user,
        "action": action,
        **details,
    }
    try:
        with open(ACCESS_LOG_PATH, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as e:
        # Logging must never take the pipeline down. Surface loudly instead.
        logger.error("Failed to write access log entry: %s", e)
