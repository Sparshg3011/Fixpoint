#!/usr/bin/env python
"""Run the bounded replan loop across the subset; write patches for grading.

This is the Step-4 runner (the loop) — sibling of run_singleshot.py (one shot).
For each instance it retrieves the top-K files, then runs the reproduce ->
patch -> replan loop, and records the best patch plus the trajectory.

    python scripts/run_loop.py --n 5 [--k 5] [--attempts 3] [--workers 3]

The loop is heavy (a reproducer run + up to `attempts` patches, each with a
Docker sandbox run), so start with a small --n. Then grade the resolve rate:

    python scripts/grade_predictions.py --predictions data/loop/predictions.jsonl --n <same n>
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fixpoint.agent.llm import DEFAULT_MODEL
from fixpoint.agent.loop import solve
from fixpoint.agent.secrets import require_api_key
from fixpoint.bench import Instance, agent_view, load_lite
from fixpoint.diary import Diary
from fixpoint.eval import lite_subset
from fixpoint.eval.images import image_key
from fixpoint.eval.recall import first_hit_rank, gold_files
from fixpoint.eval.singleshot import git_apply_check
from fixpoint.retrieval import load_corpus, tree_at
from fixpoint.retrieval.rank import ranked_files

BASE_OUT = Path(__file__).resolve().parent.parent / "data" / "loop"


def out_dir(model: str) -> Path:
    """Per-model output directory.

    Runs are namespaced by model because comparing backends is the point —
    a flat directory means the second run silently destroys the first one's
    patches and results (learned the hard way).
    """
    return BASE_OUT / model.replace("/", "_").replace(":", "_")



def run_one(inst: Instance, k: int, attempts: int, model: str) -> dict:
    """Retrieve, then run the loop. Returns a JSON-able record."""
    av = agent_view(inst)  # firewall: the loop sees only this + the image string
    # One diary per run: the UI renders these events live (SSE) and as replay.
    diary = Diary(run_id=f"{inst.instance_id}-{int(time.time())}", instance_id=inst.instance_id)
    diary.record("retrieval", "started", query_chars=len(av.problem_statement))
    tree = tree_at(inst.repo, inst.base_commit)
    docs = load_corpus(tree)
    by_path = {d.path: d.text for d in docs}
    ranked = ranked_files(docs, av.problem_statement, k=k)
    files = {p: by_path[p] for p in ranked}
    diary.record("retrieval", "succeeded", files=ranked, corpus_size=len(docs))

    result = solve(av.problem_statement, files, image_key(inst), inst.base_commit,
                   max_attempts=attempts, model=model, diary=diary, corpus=by_path)
    gold = list(gold_files(inst))
    return {
        "instance_id": inst.instance_id,
        "diff": result.diff,
        # Same real `git apply --check` as single-shot rows, so the scoreboard's
        # apply column means one thing across modes. (The sandbox applies the
        # patch too, but the broken-compass path returns a diff it never ran.)
        "applied": bool(result.diff) and git_apply_check(tree, result.diff),
        "loop_green": result.green,
        "reproducer_valid": result.reproducer_valid,
        "attempts": result.attempts,
        "cost_usd": result.cost_usd,
        "top_files": ranked,
        "gold_files": gold,
        "gold_retrieved_rank": first_hit_rank(ranked, gold),
        "trajectory": [dataclasses.asdict(s) for s in result.trajectory],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    require_api_key()
    instances = lite_subset(load_lite(), args.n)
    print(f"replan loop: {len(instances)} instances, k={args.k}, attempts={args.attempts}, "
          f"model={args.model}, {args.workers} workers")

    started = time.time()
    records: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one, inst, args.k, args.attempts, args.model): inst.instance_id
                   for inst in instances}
        for fut in as_completed(futures):
            iid = futures[fut]
            try:
                records[iid] = fut.result()
            except Exception as e:
                print(f"  ! {iid}: {type(e).__name__}: {e}")
                records[iid] = None
    wall = round(time.time() - started, 1)

    ok = [records[i.instance_id] for i in instances if records.get(i.instance_id)]
    green = sum(r["loop_green"] for r in ok)
    cost = sum(r["cost_usd"] for r in ok)
    print(f"\nreplan loop [{args.model}] — n={len(ok)}, {wall}s, ${cost:.4f} total")
    print(f"  loop-green   {green}/{len(ok)}   (agent's own reproducer satisfied)")
    print("  (harness resolve rate comes next, via grade_predictions.py)\n")
    print(f"  {'instance':<40}{'loop_green':<12}{'attempts':<10}{'repro_valid'}")
    for inst in instances:
        r = records.get(inst.instance_id)
        if not r:
            print(f"  {inst.instance_id:<40}{'CRASH':<12}")
            continue
        print(f"  {inst.instance_id:<40}{str(r['loop_green']):<12}{r['attempts']:<10}{r['reproducer_valid']}")

    OUT_DIR = out_dir(args.model)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    model_tag = f"fixpoint-loop-{args.model}"
    with (OUT_DIR / "predictions.jsonl").open("w") as f:
        for inst in instances:
            r = records.get(inst.instance_id)
            f.write(json.dumps({"instance_id": inst.instance_id, "model_name_or_path": model_tag,
                                "model_patch": (r["diff"] if r else "")}) + "\n")
    (OUT_DIR / "results.json").write_text(json.dumps({
        "model": args.model, "n": len(ok), "k": args.k, "attempts": args.attempts,
        "wall_s": wall, "loop_green": green, "total_cost_usd": round(cost, 4),
        "rows": ok,
    }, indent=2))
    print(f"\npredictions -> {OUT_DIR / 'predictions.jsonl'}")
    print(f"grade with: python scripts/grade_predictions.py --predictions "
          f"{OUT_DIR / 'predictions.jsonl'} --n {args.n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
