#!/usr/bin/env python3
"""Official entry point: answer every problem with one letter, A to E.

    python3 predict.py --input problems.jsonl --output predictions.csv
    python3 predict.py --input problems.jsonl --output predictions.csv --rag 1

Input: one JSON object per line with at least "id" and "text", plus
"options" ({"A": ..., "E": ...}) and "language" when available.
Output: a CSV with id, language, answer, seconds, status.

Rules: runs offline, at most 60 seconds per problem, deterministic.
"""
import argparse
import csv
import json
import os
import sys

from copilot.pipeline import solve


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--rag", type=int, default=0, metavar="K", help="add K similar solved problems")
    p.add_argument("--index", default="data/index.db")
    p.add_argument("--timeout", type=float, default=55.0, help="per-problem timeout in seconds (limit <=60s, target <=55s)")
    args = p.parse_args()

    index = None
    if args.rag:
        if not os.path.exists(args.index):
            sys.exit(f"{args.index} not found. Build it first: python3 build_index.py")
        from copilot.rag import Index
        index = Index(args.index)

    with open(args.input, "r", encoding="utf-8") as f:
        problems = [json.loads(line) for line in f if line.strip()]

    # Limit per problem strictly <= 60s (targeting <=25s)
    effective_timeout = min(max(5.0, float(args.timeout)), 60.0)

    with open(args.output, "w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(["id", "language", "answer", "seconds", "status"])
        for n, problem in enumerate(problems, 1):
            prob_id = str(problem.get("id") or problem.get("problem_id") or f"PROB-{n:03d}")
            problem.setdefault("id", prob_id)
            lang = str(problem.get("language") or problem.get("lang") or "eng")
            problem.setdefault("language", lang)

            result = solve(problem, index=index, k=args.rag, time_limit=effective_timeout)
            writer.writerow([prob_id, lang, result["answer"],
                             result["seconds"], result["status"]])
            out.flush()
            print(f"[{n}/{len(problems)}] {prob_id} ({lang}) -> {result['answer']} "
                  f"in {result['seconds']:.1f} s ({result['status']})", file=sys.stderr)


if __name__ == "__main__":
    main()
