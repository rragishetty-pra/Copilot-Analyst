"""
Shared data structures passed between parser -> chunker -> embedder -> vector store.
Kept as plain dataclasses (not pydantic) since nothing here crosses an API boundary yet.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TableBlock:
    page_number: int
    table_index: int          # position of this table on the page (0-indexed)
    markdown: str              # table serialized as markdown for embedding + display
    row_count: int
    col_count: int


@dataclass
class PageContent:
    page_number: int           # 1-indexed, matches what a human sees in a PDF viewer
    text: str                  # cleaned running text for the page (tables excluded)
    section_heading: Optional[str]  # best-guess nearest heading above this page's content
    tables: list = field(default_factory=list)  # list[TableBlock]


@dataclass
class ParsedDocument:
    doc_id: str                # sha256 of file bytes -> stable, content-addressed, idempotent
    doc_name: str               # original filename, e.g. "3M_2018_10K.pdf"
    source_path: str
    page_count: int
    pages: list = field(default_factory=list)  # list[PageContent]


@dataclass
class Chunk:
    chunk_id: str               # f"{doc_id}:{page_number}:{chunk_index}"
    doc_id: str
    doc_name: str
    page_number: int
    section_heading: Optional[str]
    chunk_type: str             # "text" | "table"
    chunk_index: int            # position of this chunk within its page
    text: str                   # the actual content that gets embedded
    token_count: int
