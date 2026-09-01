#!/usr/bin/env python3
"""
Quick manual retrieval test — not the answer-generation layer (that's Claude
API + this project's citation/synthesis rules, still to be built), just a way
to confirm the vector store returns the right document/page for a question
before building anything on top of it.

Usage:
    python scripts/query.py "What was total revenue for fiscal year 2024?"
"""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vectorstore import local_store  # noqa: E402
from security.access_log import log_event  # noqa: E402

logging.basicConfig(level=logging.WARNING)


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/query.py \"your question\"")
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    hits = local_store.query(question, top_k=5)
    log_event("query", question=question, result_count=len(hits))

    if not hits:
        print("Not found in this data.")
        return

    print(f'Question: "{question}"\n')
    for i, h in enumerate(hits, 1):
        print(f"--- Result {i} (score={h['score']:.3f}) ---")
        print(f"Document: {h['doc_name']}  |  Page: {h['page_number']}  |  "
              f"Section: {h['section_heading'] or '(none detected)'}  |  Type: {h['chunk_type']}")
        preview = h["text"][:300].replace("\n", " ")
        print(f"Text: {preview}{'...' if len(h['text']) > 300 else ''}\n")


if __name__ == "__main__":
    main()
