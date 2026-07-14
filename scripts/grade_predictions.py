#!/usr/bin/env python
"""Grade a predictions file with the official harness; report the resolve rate.

    python scripts/grade_predictions.py --predictions data/singleshot/predictions.jsonl --n 25

This is the honest scoreboard: apply our diff, apply the hidden test_patch, run
the scoped tests, count RESOLVED. The agent never touches this path.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fixpoint.bench import load_lite
from fixpoint.eval import lite_subset
from fixpoint.harness import read_instance_report, run_official_eval, summarize_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--n", type=int, default=25)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--namespace", default="swebench", choices=["swebench", "none"])
    parser.add_argument("--max-workers", type=int, default=3)
    args = parser.parse_args()

    preds_path = Path(args.predictions)
    preds = [json.loads(line) for line in preds_path.read_text().splitlines() if line.strip()]
    model_name = preds[0]["model_name_or_path"]
    # Only grade instances that actually have a non-empty patch — an empty patch
    # is a guaranteed unresolved and the harness skips it, so count it as such
    # without spending a container on it.
    with_patch = [p["instance_id"] for p in preds if p.get("model_patch", "").strip()]
    empty = [p["instance_id"] for p in preds if not p.get("model_patch", "").strip()]

    subset_ids = {i.instance_id for i in lite_subset(load_lite(), args.n)}
    ids = [i for i in with_patch if i in subset_ids]
    run_id = args.run_id or f"singleshot-{int(time.time())}"
    print(f"grading {len(ids)} non-empty predictions (skipping {len(empty)} empty) via {model_name}")

    started = time.time()
    run_official_eval(preds_path, run_id=run_id, instance_ids=ids,
                      namespace=args.namespace, max_workers=args.max_workers)
    wall = round(time.time() - started, 1)

    resolved, unresolved, errored = [], [], []
    for iid in ids:
        try:
            s = summarize_report(read_instance_report(run_id, model_name, iid))
            (resolved if s["resolved"] else unresolved).append(iid)
        except FileNotFoundError:
            errored.append(iid)

    total = len(subset_ids)
    print(f"\nsingle-shot resolve rate — {model_name}, {wall}s")
    print(f"  RESOLVED   {len(resolved)}/{total} = {len(resolved)/total:.1%}")
    print(f"  unresolved {len(unresolved)}   empty {len(empty)}   errored {len(errored)}")
    if resolved:
        print("  resolved:", ", ".join(resolved))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
