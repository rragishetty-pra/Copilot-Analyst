#!/usr/bin/env python3
"""
CLI entrypoint for the full answer-generation pipeline — question in,
grounded + cited answer out. This is the pre-UI validation harness for
answer_generation/answer.py, the same role scripts/query.py plays for
retrieval alone (which has no LLM call and just shows raw chunks).

Usage:
    python scripts/ask.py "What was total revenue for fiscal year 2024?"
    python scripts/ask.py "..." --session my-session   # reuse conversational memory
    python scripts/ask.py "..." --trace                # print the hidden reasoning trace too
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from answer_generation.answer import ask  # noqa: E402

logging.basicConfig(level=logging.WARNING)


def render(result: dict, show_trace: bool) -> None:
    status = result["status"]
    print(f"\n{'=' * 70}")
    print(f"STATUS: {status}   |   model: {result.get('model_used')} "
          f"({result.get('model_routing_reason')})   |   {result.get('elapsed_seconds')}s")
    print("=" * 70)

    if status == "not_found":
        print("\nNot found in this data.")

    elif status == "error":
        print(f"\n[ERROR] {result.get('error')}")

    elif status in ("answered", "partial"):
        print(f"\n{result['answer']}")
        if status == "partial" and result.get("partial_note"):
            print(f"\n(Partial answer: {result['partial_note']})")
        print("\nSources:")
        for c in result.get("citations", []):
            print(f"  - {c['doc_name']}, p.{c['page_number']} — {c['concept']}")
            print(f"      via: \"{c['sourced_via'][:150]}\"")
            print(f"      relevance: {c['relevance']}")

    elif status == "needs_clarification_conflict":
        print(f"\n{result.get('clarification_question')}")
        print("\nConflicting sources found:")
        for s in result.get("conflicting_sources") or []:
            print(f"  - {s['doc_name']}, p.{s['page_number']}: {s['value']}")

    elif status == "needs_clarification_concept":
        print(f"\n{result.get('clarification_question')}")
        print("\nDefinitions found:")
        for d in result.get("concept_definitions") or []:
            print(f"  - {d['doc_name']}, p.{d['page_number']}: {d['definition']}")

    if show_trace and result.get("reasoning_trace"):
        print(f"\n--- reasoning trace (hidden by default) ---\n{result['reasoning_trace']}")

    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--session", default=None, help="Session id for conversational memory (3-min window)")
    ap.add_argument("--trace", action="store_true", help="Show the hidden reasoning trace")
    ap.add_argument("--top-k", type=int, default=8)
    args = ap.parse_args()

    result = ask(args.question, session_id=args.session, top_k=args.top_k)
    render(result, show_trace=args.trace)


if __name__ == "__main__":
    main()
