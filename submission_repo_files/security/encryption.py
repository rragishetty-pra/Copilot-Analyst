"""
Encryption at rest for source documents (DNA doc §8: "turned ON for now...
costs close to nothing to add at this scale").

Scope, honestly stated: this encrypts the original PDF bytes stored under
data/raw using Fernet (AES-128-CBC + HMAC, via the `cryptography` package).
It does NOT yet encrypt the SQLite/vector-index files — that's a bigger lift
(either running against an encrypted volume/filesystem, or a custom
SQLCipher-backed store) than this first pass. The DNA doc (§12, risk #2)
already flags this as "revisit if it measurably slows things down" — the
honest status right now is: raw documents are encrypted, the index is not.
Worth deciding explicitly whether that's good enough before treating this
box as fully checked.

Key handling: for a single-user local prototype, a Fernet key is generated
once and stored in data/.keyfile if DOC_ENCRYPTION_KEY isn't set via env.
This is convenience for local dev, not a production key-management story —
call that out to the user rather than let it pass as more than it is.
"""
import logging
from pathlib import Path

from cryptography.fernet import Fernet

from config import RAW_DOCS_DIR, DOC_ENCRYPTION_KEY

logger = logging.getLogger(__name__)

_KEYFILE_PATH = RAW_DOCS_DIR.parent / ".keyfile"
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet

    key = DOC_ENCRYPTION_KEY.encode() if DOC_ENCRYPTION_KEY else None

    if not key and _KEYFILE_PATH.exists():
        key = _KEYFILE_PATH.read_bytes().strip()

    if not key:
        key = Fernet.generate_key()
        _KEYFILE_PATH.write_bytes(key)
        _KEYFILE_PATH.chmod(0o600)
        logger.warning(
            "No DOC_ENCRYPTION_KEY set — generated a new local key at %s. "
            "Back this up; losing it makes encrypted documents unrecoverable.",
            _KEYFILE_PATH,
        )

    _fernet = Fernet(key)
    return _fernet


def store_encrypted(source_path: str | Path, doc_id: str, kind: str = "pdf") -> Path:
    """Encrypt a file and write it into data/raw as <doc_id>.<kind>.enc.
    Returns the path to the encrypted file. The plaintext original is left
    untouched wherever the caller got it from — this module never deletes
    the user's source files.

    `kind` distinguishes the citable converted PDF ("pdf" — what the citation
    viewer serves) from the original as-uploaded source ("source" — the raw
    .htm for EDGAR filings, kept for provenance/audit even though citations
    always resolve against the PDF's page numbers). Originally only the
    converted PDF was encrypted here, leaving the raw .htm unencrypted in
    the staging area — see ingestion/pipeline.py's ingest_document, which
    now calls this for both."""
    source_path = Path(source_path)
    fernet = _get_fernet()
    plaintext = source_path.read_bytes()
    ciphertext = fernet.encrypt(plaintext)

    dest = RAW_DOCS_DIR / f"{doc_id}.{kind}.enc"
    dest.write_bytes(ciphertext)
    logger.info("Stored encrypted copy of %s -> %s", source_path.name, dest.name)
    return dest


def load_decrypted_bytes(doc_id: str, kind: str = "pdf") -> bytes:
    """Decrypt an at-rest document back into memory (e.g. to re-serve it to
    the citation viewer). Callers should avoid writing this back to disk
    unencrypted except to a short-lived temp file that's cleaned up."""
    enc_path = RAW_DOCS_DIR / f"{doc_id}.{kind}.enc"
    if not enc_path.exists():
        # Back-compat: documents ingested before the kind suffix was added
        # used a bare "<doc_id>.enc" filename for the PDF.
        legacy_path = RAW_DOCS_DIR / f"{doc_id}.enc"
        if kind == "pdf" and legacy_path.exists():
            enc_path = legacy_path
        else:
            raise FileNotFoundError(f"No encrypted document found for doc_id={doc_id} kind={kind}")
    fernet = _get_fernet()
    return fernet.decrypt(enc_path.read_bytes())
