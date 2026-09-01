"""
Conversational memory (DNA doc §6): follow-up questions use prior context if
the user stays active within ~3 minutes; after 3+ minutes idle, the next
question starts a fresh, independent context.

SQLite-backed (not just an in-memory dict) so this survives a dev-server
reload and works identically whether it's called from the CLI test harness
(scripts/ask.py) or the Flask app (app.py) — same seam pattern as the rest
of this codebase: one small module, swappable later if this ever needs to
be, say, Redis-backed for multi-user.
"""
import json
import sqlite3
import time

from config import SESSIONS_DB_PATH, SESSION_IDLE_WINDOW_SECONDS

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    last_active REAL NOT NULL,
    history TEXT NOT NULL  -- JSON list of {"role": "user"|"assistant", "content": str}
);
"""


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(SESSIONS_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def get_history(session_id: str) -> list[dict]:
    """Returns the session's turn history if it's still within the idle
    window, otherwise an empty list (fresh context) — this IS the 3-minute
    rule, applied at read time rather than via a background expiry job."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT last_active, history FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    if row is None:
        return []
    if time.time() - row["last_active"] > SESSION_IDLE_WINDOW_SECONDS:
        return []
    return json.loads(row["history"])


def append_turn(session_id: str, role: str, content: str) -> None:
    history = get_history(session_id)
    history.append({"role": role, "content": content})
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO sessions (session_id, last_active, history)
               VALUES (?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET last_active = ?, history = ?""",
            (session_id, time.time(), json.dumps(history), time.time(), json.dumps(history)),
        )
