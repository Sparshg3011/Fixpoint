#!/usr/bin/env python
"""Measure a retriever on the deterministic subset and print recall@k.

    python scripts/eval_retrieval.py --retriever mention --n 25
    python scripts/eval_retrieval.py --retriever bm25 --n 25

Misses are printed with the gold file next to the top-5, because a recall
number without its failure cases teaches nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fixpoint.bench import load_lite
from fixpoint.eval import lite_subset
from fixpoint.eval.recall import evaluate
from fixpoint.retrieval.baseline import MentionSearcher
from fixpoint.retrieval.bm25 import BM25Searcher

FACTORIES = {
    "mention": MentionSearcher,
    "bm25": BM25Searcher,
}

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "retrieval"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retriever", required=True, choices=sorted(FACTORIES))
    parser.add_argument("--n", type=int, default=25)
    parser.add_argument("--ks", type=int, nargs="+", default=[1, 5, 10])
    args = parser.parse_args()

    instances = lite_subset(load_lite(), args.n)
    result = evaluate(FACTORIES[args.retriever], instances, ks=args.ks)

    print(f"\n{args.retriever} on n={result['n']} subset")
    for k, v in result["recall"].items():
        print(f"  recall@{k:<3} {v:.2%}")
    misses = [r for r in result["rows"] if r["first_hit_rank"] is None or r["first_hit_rank"] > 5]
    if misses:
        print(f"\nmisses (gold not in top 5) — {len(misses)}:")
        for r in misses:
            print(f"  {r['instance_id']}: gold={r['gold']}")
            for i, p in enumerate(r["top"][:5], 1):
                print(f"    {i}. {p}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{args.retriever}-n{args.n}.json"
    out.write_text(json.dumps(result, indent=2))
    print(f"\nsaved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
