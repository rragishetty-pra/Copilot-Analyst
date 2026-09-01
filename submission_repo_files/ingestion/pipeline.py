"""
Orchestrates the full ingest path:

    [.htm/.html -> convert to PDF, cached] -> parse -> chunk -> write to
    SQLite -> encrypt+store the PDF -> log -> rebuild the search index once
    per run (see note below)

This is the module the CLI (and later the FastAPI upload endpoint / Drive
sync step) calls into — none of those should know the internal order of
operations, including the HTML conversion step: hand ingest_document() a
.htm or a .pdf and it does the right thing either way.

Why HTML gets converted rather than parsed directly: real SEC filings come
off EDGAR as .htm, but the DNA doc's citation model (§5) is built entirely
around "document + page number," and HTML has no pages. Converting once,
up front, with a real browser engine (see ingestion/html_converter.py) means
every page number downstream — in chunks, in citations, in the eventual PDF
viewer — refers to one fixed, reproducible rendering of the filing, not
something invented at chunk time.

One TF-IDF-specific detail: the search index is a function of the *whole*
corpus's vocabulary, not just one document, so it isn't rebuilt after every
single file — ingest_folder() rebuilds once at the end. ingest_document()
rebuilds immediately since it doesn't know whether more files are coming.
(A dense embedding backend like BGE wouldn't have this constraint — each
passage embeds independently of the rest of the corpus. This is one of the
concrete costs of the TF-IDF fallback, not just a stylistic choice.)
"""
import hashlib
import logging
import time
from pathlib import Path

from ingestion.parser import parse_pdf
from ingestion.chunker import chunk_document
from ingestion.html_converter import convert_html_to_pdf
from vectorstore import local_store
from security.encryption import store_encrypted
from security.access_log import log_event
from config import PROCESSED_DIR

logger = logging.getLogger(__name__)

HTML_EXTENSIONS = {".htm", ".html"}


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _resolve_to_pdf(path: Path) -> Path:
    """Returns a PDF path ready for parse_pdf(). Converts .htm/.html inputs
    via Playwright, caching the result by the source file's content hash so
    re-ingesting an unchanged filing (or re-running a folder ingest) doesn't
    re-render it — conversion is the slowest step in this pipeline by far."""
    if path.suffix.lower() == ".pdf":
        return path

    if path.suffix.lower() not in HTML_EXTENSIONS:
        raise ValueError(
            f"{path.name}: unsupported input type (only .pdf, .htm, .html are handled)."
        )

    source_hash = _hash_file(path)
    cached_pdf = PROCESSED_DIR / f"{source_hash}.pdf"
    if cached_pdf.exists():
        logger.info("Using cached PDF conversion of %s -> %s", path.name, cached_pdf.name)
        return cached_pdf

    logger.info("Converting %s to PDF (Playwright)...", path.name)
    t0 = time.time()
    convert_html_to_pdf(path, cached_pdf)
    logger.info("Converted %s -> %s in %.1fs", path.name, cached_pdf.name, time.time() - t0)
    log_event("html_converted", source_name=path.name, pdf_name=cached_pdf.name)
    return cached_pdf


def ingest_document(path: str | Path, force: bool = False, rebuild: bool = True) -> dict:
    """Ingest a single document (.pdf, .htm, or .html) end to end. Returns a
    summary dict.

    force=True re-ingests even if this exact file (by content hash of the
    resulting PDF) has already been indexed — useful during development;
    the default behavior respects the DNA doc's idempotency expectation.

    rebuild=False skips the index rebuild step, for callers (like
    ingest_folder) that want to batch several documents before paying the
    refit cost once.
    """
    path = Path(path)
    t_start = time.time()
    was_html = path.suffix.lower() in HTML_EXTENSIONS

    t0 = time.time()
    pdf_path = _resolve_to_pdf(path)
    convert_seconds = round(time.time() - t0, 1) if was_html else 0.0

    t0 = time.time()
    parsed = parse_pdf(pdf_path)
    parsed.doc_name = path.name  # keep the ORIGINAL filename in citations, not "<hash>.pdf"
    parse_seconds = round(time.time() - t0, 1)

    if not force and local_store.doc_already_ingested(parsed.doc_id):
        logger.info("Skipping %s — already ingested (doc_id=%s). Use force=True to re-ingest.",
                     parsed.doc_name, parsed.doc_id)
        log_event("ingest_skipped", doc_id=parsed.doc_id, doc_name=parsed.doc_name)
        return {"doc_name": parsed.doc_name, "doc_id": parsed.doc_id, "status": "skipped"}

    t0 = time.time()
    chunks = chunk_document(parsed)
    chunk_seconds = round(time.time() - t0, 1)
    if not chunks:
        logger.warning("%s produced zero chunks — check that the document has extractable text.",
                        parsed.doc_name)
        log_event("ingest_empty", doc_id=parsed.doc_id, doc_name=parsed.doc_name)
        return {"doc_name": parsed.doc_name, "doc_id": parsed.doc_id, "status": "empty"}

    t0 = time.time()
    local_store.add_chunks(chunks)
    store_encrypted(pdf_path, parsed.doc_id, kind="pdf")  # the citable artifact — see module docstring
    if was_html:
        # Also encrypt the original as-uploaded .htm, not just the converted
        # PDF — closes the gap where raw source files sat unencrypted in the
        # staging area (DNA doc §8: encryption at rest, no carve-outs).
        store_encrypted(path, parsed.doc_id, kind="source")
    store_seconds = round(time.time() - t0, 1)

    embed_seconds = 0.0
    if rebuild:
        t0 = time.time()
        local_store.rebuild_index()
        embed_seconds = round(time.time() - t0, 1)

    total_elapsed = round(time.time() - t_start, 1)
    summary = {
        "doc_name": parsed.doc_name,
        "doc_id": parsed.doc_id,
        "status": "ingested",
        "page_count": parsed.page_count,
        "chunk_count": len(chunks),
        "convert_seconds": convert_seconds,
        "parse_seconds": parse_seconds,
        "chunk_seconds": chunk_seconds,
        "store_seconds": store_seconds,
        "embed_seconds": embed_seconds,
        "elapsed_seconds": total_elapsed,
    }
    log_event("ingest", **summary)
    logger.info("Ingested %s: %d pages, %d chunks, %.1fs total "
                "(convert=%.1fs parse=%.1fs chunk=%.1fs store=%.1fs embed=%.1fs)",
                parsed.doc_name, parsed.page_count, len(chunks), total_elapsed,
                convert_seconds, parse_seconds, chunk_seconds, store_seconds, embed_seconds)
    return summary


def ingest_folder(folder: str | Path, force: bool = False) -> list[dict]:
    folder = Path(folder)
    docs = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in ({".pdf"} | HTML_EXTENSIONS)
    )
    if not docs:
        logger.warning("No .pdf/.htm/.html files found in %s", folder)
        return []

    results = []
    any_new = False
    for doc_path in docs:
        try:
            result = ingest_document(doc_path, force=force, rebuild=False)
            results.append(result)
            if result["status"] == "ingested":
                any_new = True
        except Exception as e:
            logger.error("Failed to ingest %s: %s", doc_path.name, e)
            log_event("ingest_error", doc_name=doc_path.name, error=str(e))
            results.append({"doc_name": doc_path.name, "status": "error", "error": str(e)})

    if any_new:
        local_store.rebuild_index()

    return results
