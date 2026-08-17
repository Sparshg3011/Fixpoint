#!/usr/bin/env python
"""Run the single-shot patcher across the subset; report apply-rate and cost.

This is Step 3's gate measurement: what fraction of generated diffs apply
cleanly with real git apply. It also writes a predictions file the official
harness grades for the single-shot RESOLVE rate (a separate, slower step).

    python scripts/run_singleshot.py --n 25 [--k 5] [--workers 5]

Nothing here is hardcoded: every diff comes from a live model call over
retrieved files, and every apply verdict comes from real git apply.
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
from fixpoint.agent.secrets import require_api_key
from fixpoint.bench import load_lite
from fixpoint.eval import lite_subset
from fixpoint.eval.singleshot import SingleShotResult, run_single

BASE_OUT = Path(__file__).resolve().parent.parent / "data" / "singleshot"


def out_dir(model: str) -> Path:
    """Per-model output directory.

    Runs are namespaced by model because comparing backends is the point —
    a flat directory means the second run silently destroys the first one's
    patches and results (learned the hard way).
    """
    return BASE_OUT / model.replace("/", "_").replace(":", "_")


def load_checkpoint(path: Path, k: int) -> dict[str, SingleShotResult]:
    """Rows persisted by a previous (interrupted) run, keyed by instance id.

    Why this exists: a Lite-300 generation on a throttled free tier runs for
    many hours, and the first implementation held every finished row in memory
    until the end — one crash or kill and a night of API results evaporated.
    Now each row lands on disk the moment it completes, and a rerun only pays
    for what is actually missing.

    Rows are only reusable at the same k (different k = different retrieval =
    a different experiment); a torn final line from a mid-write kill is skipped.
    """
    fields = {f.name for f in dataclasses.fields(SingleShotResult)}
    rows: dict[str, SingleShotResult] = {}
    if not path.exists():
        return rows
    for ln in path.read_text().splitlines():
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if rec.get("k") == k:
            # Keep only dataclass fields: rows that passed through the rescue
            # tool carry a provenance key ("rescued") the constructor rejects.
            row = {kk: v for kk, v in rec["row"].items() if kk in fields}
            rows[row["instance_id"]] = SingleShotResult(**row)
    return rows



def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=25)
    parser.add_argument("--k", type=int, default=5, help="candidate files retrieved per instance")
    parser.add_argument("--workers", type=int, default=5, help="concurrent LLM calls")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--fresh", action="store_true",
                        help="ignore an existing checkpoint and regenerate everything")
    args = parser.parse_args()

    require_api_key()  # loads .env; exits with instructions if absent
    instances = lite_subset(load_lite(), args.n)
    print(f"single-shot: {len(instances)} instances, k={args.k} files, "
          f"model={args.model}, {args.workers} workers", flush=True)

    OUT_DIR = out_dir(args.model)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ckpt_path = OUT_DIR / "checkpoint.jsonl"
    if args.fresh:
        ckpt_path.unlink(missing_ok=True)
    wanted = {i.instance_id for i in instances}
    results: dict[str, SingleShotResult | None] = {
        iid: row for iid, row in load_checkpoint(ckpt_path, args.k).items() if iid in wanted}
    if results:
        print(f"resuming — {len(results)} instances already generated", flush=True)
    todo = [i for i in instances if i.instance_id not in results]

    started = time.time()
    done_count = len(results)
    # I/O-bound (each task is dominated by one LLM call), so a small thread pool
    # cuts wall time without hammering the rate limit.
    with ThreadPoolExecutor(max_workers=args.workers) as pool, \
            ckpt_path.open("a") as ckpt:
        futures = {pool.submit(run_single, inst, args.k, args.model): inst.instance_id
                   for inst in todo}
        try:
            for fut in as_completed(futures):
                iid = futures[fut]
                done_count += 1
                try:
                    r = results[iid] = fut.result()
                except Exception as e:  # a whole-instance failure is a data point, not a crash
                    print(f"  ! [{done_count}/{len(instances)}] {iid}: {type(e).__name__}: {e}",
                          flush=True)
                    results[iid] = None
                    continue
                # Land the row before moving on — flushed, so a kill loses at
                # most the row in flight, never the run.
                ckpt.write(json.dumps({"k": args.k, "row": vars(r)}) + "\n")
                ckpt.flush()
                print(f"  [{done_count}/{len(instances)}] {iid} "
                      f"applied={r.applied}{' error=' + r.error if r.error else ''}", flush=True)
        except KeyboardInterrupt:
            # Ctrl-C means STOP NOW — everything finished so far is already
            # checkpointed; don't let the pool run the whole queue first.
            pool.shutdown(wait=False, cancel_futures=True)
            raise
    wall = round(time.time() - started, 1)

    ordered = [results[i.instance_id] for i in instances if results.get(i.instance_id)]
    n = len(ordered)
    applied = sum(r.applied for r in ordered)
    localized = sum(1 for r in ordered if r.gold_retrieved_rank is not None and r.gold_retrieved_rank <= args.k)
    cost = sum(r.cost_usd for r in ordered)

    print(f"\nsingle-shot [{args.model}] — n={n}, {wall}s, ${cost:.4f} total (${cost/max(n,1):.4f}/instance)")
    print(f"  apply rate       {applied}/{n} = {applied/max(n,1):.1%}   (gate: >=90%)")
    print(f"  localization@{args.k}   {localized}/{n} = {localized/max(n,1):.1%}   (gold file was retrieved)")
    print("\nper-instance:")
    print(f"  {'instance':<40}{'applied':<9}{'gold@k':<8}{'error'}")
    for inst in instances:
        r = results.get(inst.instance_id)
        if r is None:
            print(f"  {inst.instance_id:<40}{'CRASH':<9}")
            continue
        gold_mark = "yes" if (r.gold_retrieved_rank and r.gold_retrieved_rank <= args.k) else "MISS"
        print(f"  {inst.instance_id:<40}{('yes' if r.applied else 'NO'):<9}{gold_mark:<8}{r.error or ''}")

    # predictions.jsonl for the official harness (single-shot resolve rate).
    model_tag = f"fixpoint-singleshot-{args.model}"
    with (OUT_DIR / "predictions.jsonl").open("w") as f:
        for inst in instances:
            r = results.get(inst.instance_id)
            f.write(json.dumps({
                "instance_id": inst.instance_id,
                "model_name_or_path": model_tag,
                "model_patch": (r.diff if r else ""),
            }) + "\n")
    # full results for analysis (diffs, errors, retrieval, cost).
    (OUT_DIR / "results.json").write_text(json.dumps({
        "model": args.model, "n": n, "k": args.k, "wall_s": wall,
        "apply_rate": applied / max(n, 1), "localization_at_k": localized / max(n, 1),
        "total_cost_usd": round(cost, 4),
        "rows": [vars(r) for r in ordered],
    }, indent=2))
    # The checkpoint has served its purpose once every instance has a row;
    # keeping it would let a FUTURE run with different intent silently reuse
    # stale rows. Crashed instances keep it alive so a rerun retries just them.
    if all(results.get(i.instance_id) is not None for i in instances):
        ckpt_path.unlink(missing_ok=True)

    print(f"\npredictions -> {OUT_DIR / 'predictions.jsonl'}")
    print(f"results     -> {OUT_DIR / 'results.json'}")
    print("\nnext: grade single-shot resolve rate with the official harness:")
    print(f"  python scripts/grade_predictions.py --predictions {OUT_DIR / 'predictions.jsonl'} --n {args.n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
