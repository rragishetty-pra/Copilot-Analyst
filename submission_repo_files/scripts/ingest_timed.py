#!/usr/bin/env python3
"""
Single-file ingestion with a full stage-by-stage timing breakdown, appended
to data/vector_store/ingestion_timings.csv. Used for the batch corpus run —
each file's "upload" (device-to-container staging) happens outside this
script (it's an MCP tool call, not something a subprocess can do), so its
duration is passed in via --stage-seconds and merged into the same row.

Usage:
    python scripts/ingest_timed.py path/to/file.htm --stage-seconds 3.2
"""
import argparse
import csv
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.pipeline import ingest_document  # noqa: E402
from config import STORE_DIR  # noqa: E402

logging.basicConfig(level=logging.WARNING)  # keep stdout terse; timings print explicitly below

TIMINGS_CSV = STORE_DIR / "ingestion_timings.csv"
FIELDS = ["doc_name", "status", "page_count", "chunk_count", "stage_seconds",
          "convert_seconds", "parse_seconds", "chunk_seconds", "store_seconds",
          "embed_seconds", "elapsed_seconds", "total_with_stage_seconds"]


def append_row(row: dict) -> None:
    is_new = not TIMINGS_CSV.exists()
    with open(TIMINGS_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in FIELDS})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--stage-seconds", type=float, default=0.0)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    result = ingest_document(args.file, force=args.force, rebuild=True)
    result["stage_seconds"] = round(args.stage_seconds, 1)
    result["total_with_stage_seconds"] = round(
        args.stage_seconds + result.get("elapsed_seconds", 0), 1
    )
    append_row(result)

    if result["status"] == "ingested":
        print(f"[OK] {result['doc_name']}: {result['page_count']}p {result['chunk_count']}c | "
              f"stage={result['stage_seconds']}s convert={result['convert_seconds']}s "
              f"parse={result['parse_seconds']}s chunk={result['chunk_seconds']}s "
              f"store={result['store_seconds']}s embed={result['embed_seconds']}s | "
              f"total={result['total_with_stage_seconds']}s")
    else:
        print(f"[{result['status'].upper()}] {result['doc_name']}")


if __name__ == "__main__":
    main()
