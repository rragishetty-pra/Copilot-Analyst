"""
PDF parsing: born-digital PDFs only (DNA doc §3 — no OCR path in v1).

Built entirely on pdfplumber (already available in this environment — see
embeddings/embedder.py for why heavier options like PyMuPDF weren't an option
here). pdfplumber wraps pdfminer.six and gives us three things from one pass
per page: running text, character-level font-size data (for heading
detection), and table extraction.

Text and tables are extracted separately, then table cell text is stripped out
of a page's running prose, so a table's numbers don't get embedded twice
(once mangled inside flattened prose, once as a clean table block).
"""
import hashlib
import logging
from collections import defaultdict
from pathlib import Path

import pdfplumber

from ingestion.models import ParsedDocument, PageContent, TableBlock

logger = logging.getLogger(__name__)


def _hash_file(path: Path) -> str:
    """Content hash -> stable doc_id. Same bytes always yield the same doc_id,
    which is what makes re-ingestion idempotent (DNA doc §3: docs are never
    updated in place, but re-running ingestion on an unchanged file shouldn't
    create duplicates)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _detect_heading(page) -> str | None:
    """Cheap but effective heading heuristic for financial filings, which are
    heavily sectioned (Item 1, Item 7A, Note 12, etc.): group characters into
    lines, compare each line's font size against the page's median (a proxy
    for body text size), and return the topmost line that stands out."""
    chars = page.chars
    if not chars:
        return None

    lines = defaultdict(list)
    for c in chars:
        line_key = round(c["top"])  # group characters that share a text line
        lines[line_key].append(c)

    sizes = [c["size"] for c in chars]
    body_size = sorted(sizes)[len(sizes) // 2]
    threshold = body_size * 1.15

    candidates = []
    for top, line_chars in lines.items():
        text = "".join(c["text"] for c in sorted(line_chars, key=lambda c: c["x0"])).strip()
        if not text or len(text) > 120:
            continue
        line_size = max(c["size"] for c in line_chars)
        if line_size >= threshold:
            candidates.append((top, text))

    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])  # topmost first
    return candidates[0][1]


def _extract_page_tables(page) -> list[TableBlock]:
    tables = []
    try:
        raw_tables = page.extract_tables()
    except Exception as e:
        logger.warning("Table extraction failed on page %s: %s", page.page_number, e)
        return tables

    for idx, raw in enumerate(raw_tables):
        if not raw or all(all(cell in (None, "") for cell in row) for row in raw):
            continue
        rows = [[("" if c is None else str(c).strip()) for c in row] for row in raw]
        header, *body = rows
        md_lines = ["| " + " | ".join(header) + " |",
                    "| " + " | ".join(["---"] * len(header)) + " |"]
        for row in body:
            md_lines.append("| " + " | ".join(row) + " |")
        tables.append(TableBlock(
            page_number=page.page_number,
            table_index=idx,
            markdown="\n".join(md_lines),
            row_count=len(rows),
            col_count=len(header),
        ))
    return tables


def parse_pdf(path: str | Path) -> ParsedDocument:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() != ".pdf":
        raise ValueError(
            f"{path.name}: only born-digital PDFs are supported (DNA doc §3, §10 — no OCR path)."
        )

    doc_id = _hash_file(path)
    pages: list[PageContent] = []

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            page_number = page.page_number  # pdfplumber is already 1-indexed

            raw_text = page.extract_text() or ""
            heading = _detect_heading(page)
            tables = _extract_page_tables(page)

            # Strip table cell text out of the running prose so numbers aren't
            # double-counted between a page's "text" and its "tables".
            page_text = raw_text
            for t in tables:
                for row_line in t.markdown.splitlines():
                    cells = [c.strip() for c in row_line.strip("|").split("|")]
                    for cell in cells:
                        if len(cell) > 6 and cell in page_text:
                            page_text = page_text.replace(cell, "")

            pages.append(PageContent(
                page_number=page_number,
                text=page_text.strip(),
                section_heading=heading,
                tables=tables,
            ))

    parsed = ParsedDocument(
        doc_id=doc_id,
        doc_name=path.name,
        source_path=str(path),
        page_count=len(pages),
        pages=pages,
    )
    logger.info("Parsed %s: %d pages, doc_id=%s", path.name, len(pages), doc_id)
    return parsed
