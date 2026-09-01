#!/usr/bin/env python3
"""
CLI entrypoint for ingestion.

Usage:
    python scripts/ingest.py --file path/to/doc.pdf
    python scripts/ingest.py --file path/to/filing.htm      # SEC EDGAR-style HTML also works
    python scripts/ingest.py --folder path/to/folder_of_docs
    python scripts/ingest.py --folder path/to/folder_of_docs --force

.htm/.html files are converted to PDF automatically (Playwright, cached by
content hash — see ingestion/html_converter.py and ingestion/pipeline.py's
_resolve_to_pdf) before parsing. Real filings from EDGAR come as .htm; this
means dropping one in works the same as dropping in a PDF.

Note on Google Drive: PDFs/HTML are NOT synced down automatically from the
Drive connector (binary content relayed through the model was found to
silently corrupt files — see ingestion/drive_source.py). Files reach
data/incoming/ either by being dragged into the Claude conversation, or, if
a Drive folder is synced locally via a desktop client, through the device
bridge. Once they're there, ingesting them is the same as any other local
folder:

    python scripts/ingest.py --folder data/incoming
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.pipeline import ingest_document, ingest_folder  # noqa: E402
from vectorstore import local_store  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main():
    ap = argparse.ArgumentParser(description="Ingest PDFs into the local vector store.")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="Path to a single PDF")
    group.add_argument("--folder", help="Path to a folder of PDFs")
    ap.add_argument("--force", action="store_true", help="Re-ingest even if already indexed")
    args = ap.parse_args()

    if args.file:
        results = [ingest_document(args.file, force=args.force)]
    else:
        results = ingest_folder(args.folder, force=args.force)

    print("\n--- Ingestion summary ---")
    for r in results:
        status = r.get("status")
        if status == "ingested":
            print(f"  [OK]      {r['doc_name']}: {r['page_count']} pages, "
                  f"{r['chunk_count']} chunks, {r['elapsed_seconds']}s")
        elif status == "skipped":
            print(f"  [SKIP]    {r['doc_name']}: already indexed (doc_id={r['doc_id']})")
        elif status == "empty":
            print(f"  [EMPTY]   {r['doc_name']}: no extractable text/tables")
        else:
            print(f"  [ERROR]   {r['doc_name']}: {r.get('error', 'unknown error')}")

    stats = local_store.collection_stats()
    print(f"\nCollection totals: {stats['total_chunks']} chunks across "
          f"{stats['distinct_documents']} document(s).")


if __name__ == "__main__":
    main()
