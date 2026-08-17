#!/usr/bin/env python
"""Benchmark the replan loop at scale: generate AND grade, one image-chunk at a time.

Every published number so far is single-shot — one attempt, no execution
feedback. This runner points the loop (reproducer -> patch -> replan) at a
Lite subset and grades with the official harness as it goes. The unit of work
is a (repo, version) chunk because that is the unit of Docker traffic: the
loop's sandbox and the harness need the SAME instance images, so grading a
chunk immediately after generating it means every image is pulled exactly
once, used twice, and pruned only under disk pressure.

    python scripts/run_loop_bench.py --n 100 [--attempts 3] [--workers 2]

Fully resumable: rows land in checkpoint.jsonl as they finish, verdicts merge
into graded.json after every chunk. Kill it anytime; rerun continues.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from grade_chunked import prepull, prune_if_needed  # noqa: E402
from run_loop import out_dir, run_one  # noqa: E402  (sibling script, one source of truth)

from fixpoint.agent.llm import DEFAULT_MODEL  # noqa: E402
from fixpoint.agent.secrets import require_api_key  # noqa: E402
from fixpoint.bench import load_lite  # noqa: E402
from fixpoint.eval import lite_subset  # noqa: E402
from fixpoint.eval.chunking import DEFAULT_PRUNE_THRESHOLD_GB, plan_chunks  # noqa: E402
from fixpoint.harness import read_instance_report, run_official_eval, summarize_report  # noqa: E402


def load_checkpoint(path: Path, k: int, attempts: int) -> dict[str, dict]:
    """Previously generated rows, reusable only under the same (k, attempts)."""
    rows: dict[str, dict] = {}
    if not path.exists():
        return rows
    for ln in path.read_text().splitlines():
        try:
            rec = json.loads(ln)
        except ValueError:
            continue  # torn tail from a mid-write kill
        if rec.get("k") == k and rec.get("attempts") == attempts:
            rows[rec["row"]["instance_id"]] = rec["row"]
    return rows


def atomic_write(path: Path, text: str) -> None:
    """write_text via tmp+rename: a kill mid-write must never leave a torn
    artifact — a torn graded.json would crash the resume AND lose every
    verdict accumulated so far."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def load_graded(path: Path, k: int, attempts: int, wanted: set[str]) -> dict[str, bool]:
    """Resume verdicts — only those that still describe THIS experiment.

    Two silent-corruption channels are closed here: (1) verdicts stamped with
    a different (k, attempts) belong to patches that will be REGENERATED, so
    reusing them would grade code that no longer exists; (2) verdicts for
    instances outside the current subset would inflate the resolved count when
    rerunning with a smaller --n."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except ValueError:
        print("  ! graded.json unreadable — starting verdicts fresh "
              "(harness logs still hold the originals)", flush=True)
        return {}
    stamp = data.get("experiment")
    if stamp != {"k": k, "attempts": attempts}:
        print(f"  ! graded.json is from a different experiment {stamp} — "
              f"discarding its verdicts", flush=True)
        return {}
    return {iid: v for iid, v in data.get("per_instance", {}).items() if iid in wanted}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--workers", type=int, default=2, help="concurrent loop instances")
    ap.add_argument("--grade-workers", type=int, default=4)
    ap.add_argument("--chunk-size", type=int, default=12)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--namespace", default="swebench", choices=["swebench", "none"])
    ap.add_argument("--prune-threshold-gb", type=float, default=DEFAULT_PRUNE_THRESHOLD_GB)
    args = ap.parse_args()

    require_api_key()
    instances = lite_subset(load_lite(), args.n)
    by_id = {i.instance_id: i for i in instances}
    meta = {i.instance_id: (i.repo, i.version) for i in load_lite()}

    OUT = out_dir(args.model)
    OUT.mkdir(parents=True, exist_ok=True)
    ckpt_path = OUT / "checkpoint.jsonl"
    preds_path = OUT / "predictions.jsonl"
    graded_path = OUT / "graded.json"
    model_tag = f"fixpoint-loop-{args.model}"

    # Advisory run lock: two concurrent invocations would interleave checkpoint
    # appends and race the artifact rewrites. flock releases on process death,
    # so a crashed run never wedges the directory.
    import fcntl
    lock_handle = (OUT / ".lock").open("w")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"another run is already active in {OUT} — refusing to interleave")
        return 1

    rows = {iid: r for iid, r in load_checkpoint(ckpt_path, args.k, args.attempts).items()
            if iid in by_id}
    graded = load_graded(graded_path, args.k, args.attempts, set(by_id))
    if rows or graded:
        print(f"resuming — {len(rows)} generated, {len(graded)} graded", flush=True)

    chunks = plan_chunks([i.instance_id for i in instances], meta, max_chunk=args.chunk_size)
    print(f"loop bench: {len(instances)} instances in {len(chunks)} (repo,version) chunks, "
          f"attempts={args.attempts}, model={args.model}", flush=True)

    def persist() -> None:
        """One coherent artifact set after every chunk — kill-safe by design
        (every file goes through atomic_write; a kill can lose at most the
        latest chunk's verdicts, never corrupt what's on disk)."""
        ordered = [rows[i.instance_id] for i in instances if i.instance_id in rows]
        atomic_write(preds_path, "".join(
            json.dumps({"instance_id": i.instance_id,
                        "model_name_or_path": model_tag,
                        "model_patch": (rows.get(i.instance_id) or {}).get("diff", "")}) + "\n"
            for i in instances))
        empty = sum(1 for r in ordered if not (r.get("diff") or "").strip())
        atomic_write(graded_path, json.dumps({
            "model": model_tag, "total_predictions": len(instances), "empty": empty,
            "experiment": {"k": args.k, "attempts": args.attempts},
            "graded": len(graded), "resolved": sum(graded.values()),
            "per_instance": graded,
        }, indent=2))
        atomic_write(OUT / "results.json", json.dumps({
            "model": args.model, "mode": "loop", "n": len(instances), "k": args.k,
            "attempts": args.attempts,
            "loop_green": sum(1 for r in ordered if r.get("loop_green")),
            "reproducer_valid": sum(1 for r in ordered if r.get("reproducer_valid")),
            "total_cost_usd": round(sum(r.get("cost_usd", 0.0) for r in ordered), 4),
            "rows": ordered,
        }, indent=2))

    started = time.time()
    base = f"loopbench-{int(started)}"
    for ci, chunk in enumerate(chunks, 1):
        todo = [iid for iid in chunk if iid not in rows]
        to_grade_later = [iid for iid in chunk if iid not in graded]
        if not todo and not any(
                (rows.get(iid, {}).get("diff") or "").strip() for iid in to_grade_later):
            continue  # chunk fully generated and graded on a previous run
        print(f"\nchunk {ci}/{len(chunks)} ({len(chunk)} instances, {len(todo)} to generate)",
              flush=True)

        # Images serve double duty: the loop's reproducer sandbox now, the
        # harness right after. Pull once, explicitly amd64 (Apple Silicon).
        pulled, failed = prepull(chunk, args.namespace)
        print(f"  pre-pulled {pulled}/{len(chunk)} images"
              + (f" ({failed} unavailable)" if failed else ""), flush=True)

        with ThreadPoolExecutor(max_workers=args.workers) as pool, \
                ckpt_path.open("a") as ckpt:
            futures = {pool.submit(run_one, by_id[iid], args.k, args.attempts, args.model): iid
                       for iid in todo}
            try:
                for fut in as_completed(futures):
                    iid = futures[fut]
                    try:
                        row = rows[iid] = fut.result()
                    except Exception as e:  # one instance failing is a data point
                        print(f"  ! {iid}: {type(e).__name__}: {e}", flush=True)
                        continue
                    ckpt.write(json.dumps({"k": args.k, "attempts": args.attempts,
                                           "row": row}) + "\n")
                    ckpt.flush()
                    print(f"  {iid}: green={row['loop_green']} applied={row['applied']} "
                          f"attempts={row['attempts']}", flush=True)
            except KeyboardInterrupt:
                # Ctrl-C means STOP: without this, the with-block would wait
                # for every queued instance to run to completion at full cost.
                pool.shutdown(wait=False, cancel_futures=True)
                persist()
                raise

        persist()  # predictions must exist on disk before the harness reads them

        to_grade = [iid for iid in chunk
                    if iid not in graded and (rows.get(iid, {}).get("diff") or "").strip()]
        if to_grade:
            run_id = f"{base}-c{ci}"
            try:
                run_official_eval(preds_path, run_id=run_id, instance_ids=to_grade,
                                  namespace=args.namespace, max_workers=args.grade_workers)
            except subprocess.CalledProcessError as e:
                print(f"  harness exited nonzero ({e.returncode}) — reading what it wrote",
                      flush=True)
            got = 0
            for iid in to_grade:
                try:
                    graded[iid] = bool(summarize_report(
                        read_instance_report(run_id, model_tag, iid))["resolved"])
                    got += 1
                except FileNotFoundError:
                    pass  # image/env failure: ungraded, never counted unresolved
            persist()
            print(f"  graded {got}/{len(to_grade)} — running total "
                  f"{sum(graded.values())} resolved / {len(graded)} graded", flush=True)

        prune_if_needed(args.prune_threshold_gb)

    ordered = [rows[i.instance_id] for i in instances if i.instance_id in rows]
    green = sum(1 for r in ordered if r.get("loop_green"))
    resolved = sum(graded.values())
    print(f"\nloop bench [{args.model}] — {time.time()-started:.0f}s this invocation")
    print(f"  generated   {len(ordered)}/{len(instances)}")
    print(f"  loop-green  {green}/{len(ordered)}   (agent's own reproducer satisfied)")
    print(f"  RESOLVED    {resolved}/{len(instances)} = {resolved/len(instances):.1%}   "
          f"(official harness; graded {len(graded)})")
    print(f"  -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
