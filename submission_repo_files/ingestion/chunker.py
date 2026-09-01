"""
Page-aware chunking.

The one rule that matters here: a chunk never spans more than one page.
Standard RAG chunkers happily slide a window across page boundaries to keep
chunk sizes uniform — we deliberately don't, because DNA doc §5 requires every
answer to cite a single document + page number. A chunk that mixes page 12 and
page 13 content makes that citation a lie.

Within a page: paragraph-aware packing into ~CHUNK_TARGET_WORDS chunks with a
small word-overlap (so a fact split across two paragraphs isn't lost at a
chunk boundary). Sizing is measured in whitespace-split words rather than true
LLM tokens — tiktoken isn't installable in this sandbox (see
embeddings/embedder.py), and word count is a perfectly adequate ruler for
"is this chunk roughly the right size," which is all it's used for here.

Tables are never merged into text chunks — each table becomes its own chunk,
tagged chunk_type="table", so a "what was the DTI ratio" query can retrieve the
clean table body rather than a garbled mix of prose and numbers.
"""
import re
import logging

from ingestion.models import ParsedDocument, Chunk
from config import CHUNK_TARGET_WORDS, CHUNK_OVERLAP_WORDS, MIN_CHUNK_WORDS

logger = logging.getLogger(__name__)


def _word_count(text: str) -> int:
    return len(text.split())


def _split_paragraphs(text: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[A-Z0-9])", text) if p.strip()]
    return paras if paras else ([text.strip()] if text.strip() else [])


def _pack_paragraphs(paragraphs: list[str]) -> list[str]:
    """Greedily pack paragraphs into ~CHUNK_TARGET_WORDS chunks, carrying a
    small tail of the previous chunk forward as overlap."""
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""
    current_words = 0

    for para in paragraphs:
        para_words = _word_count(para)

        if current and current_words + para_words > CHUNK_TARGET_WORDS:
            chunks.append(current.strip())
            overlap_words = current.split()[-CHUNK_OVERLAP_WORDS:]
            current = " ".join(overlap_words) + "\n\n" + para
            current_words = _word_count(current)
        else:
            current = (current + "\n\n" + para).strip() if current else para
            current_words = _word_count(current)

    if current.strip():
        chunks.append(current.strip())

    # Merge a too-small trailing fragment into the previous chunk rather than
    # storing a near-empty chunk that adds noise to retrieval.
    if len(chunks) >= 2 and _word_count(chunks[-1]) < MIN_CHUNK_WORDS:
        chunks[-2] = chunks[-2] + "\n\n" + chunks[-1]
        chunks.pop()

    return chunks


def chunk_document(parsed: ParsedDocument) -> list[Chunk]:
    all_chunks: list[Chunk] = []

    for page in parsed.pages:
        chunk_index = 0

        if page.text.strip():
            paragraphs = _split_paragraphs(page.text)
            packed = _pack_paragraphs(paragraphs)
            for text in packed:
                all_chunks.append(Chunk(
                    chunk_id=f"{parsed.doc_id}:{page.page_number}:{chunk_index}",
                    doc_id=parsed.doc_id,
                    doc_name=parsed.doc_name,
                    page_number=page.page_number,
                    section_heading=page.section_heading,
                    chunk_type="text",
                    chunk_index=chunk_index,
                    text=text,
                    token_count=_word_count(text),
                ))
                chunk_index += 1

        for table in page.tables:
            all_chunks.append(Chunk(
                chunk_id=f"{parsed.doc_id}:{page.page_number}:{chunk_index}",
                doc_id=parsed.doc_id,
                doc_name=parsed.doc_name,
                page_number=page.page_number,
                section_heading=page.section_heading,
                chunk_type="table",
                chunk_index=chunk_index,
                text=table.markdown,
                token_count=_word_count(table.markdown),
            ))
            chunk_index += 1

    logger.info("Chunked %s: %d chunks across %d pages",
                parsed.doc_name, len(all_chunks), parsed.page_count)
    return all_chunks
