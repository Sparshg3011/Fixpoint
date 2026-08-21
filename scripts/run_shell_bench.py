#!/usr/bin/env python
"""Benchmark the interactive bash agent: generate AND grade, chunk by chunk.

Same campaign shape as run_loop_bench.py — (repo, version) image chunks,
per-instance checkpoints, atomic artifacts, official-harness grading while
each chunk's images are still local — with the generator swapped for the
shell agent. No retrieval stage exists in this mode: the agent localizes
itself inside the container.

    python scripts/run_shell_bench.py --n 25 [--steps 40] [--workers 2]

Transcripts land one file per instance under transcripts/ (they are the
debugging record and the demo material; results.json stays lean).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from grade_chunked import prepull, prune_if_needed  # noqa: E402
from run_loop_bench import atomic_write  # noqa: E402

from fixpoint.agent.llm import DEFAULT_MODEL  # noqa: E402
from fixpoint.agent.secrets import require_api_key  # noqa: E402
from fixpoint.agent.shell_agent import solve_in_shell  # noqa: E402
from fixpoint.bench import Instance, agent_view, load_lite  # noqa: E402
from fixpoint.diary import Diary  # noqa: E402
from fixpoint.eval import lite_subset  # noqa: E402
from fixpoint.eval.chunking import DEFAULT_PRUNE_THRESHOLD_GB, plan_chunks  # noqa: E402
from fixpoint.eval.images import image_key  # noqa: E402
from fixpoint.eval.singleshot import git_apply_check  # noqa: E402
from fixpoint.harness import read_instance_report, run_official_eval, summarize_report  # noqa: E402
from fixpoint.retrieval import tree_at  # noqa: E402

BASE_OUT = Path(__file__).resolve().parent.parent / "data" / "shell"


def out_dir(model: str) -> Path:
    return BASE_OUT / model.replace("/", "_").replace(":", "_")


def load_checkpoint(path: Path, steps: int) -> dict[str, dict]:
    """Rows from a previous run, reusable only at the same step budget."""
    rows: dict[str, dict] = {}
    if not path.exists():
        return rows
    for ln in path.read_text().splitlines():
        try:
            rec = json.loads(ln)
        except ValueError:
            continue  # torn tail from a mid-write kill
        if rec.get("steps_budget") == steps:
            rows[rec["row"]["instance_id"]] = rec["row"]
    return rows


class RateLimited(Exception):
    """The endpoint refused before the agent did anything. Not a data point."""


def run_one(inst: Instance, steps: int, model: str, transcripts: Path) -> dict:
    av = agent_view(inst)  # firewall: issue text only
    diary = Diary(run_id=f"{inst.instance_id}-shell-{int(time.time())}",
                  instance_id=inst.instance_id)
    r = solve_in_shell(av.problem_statement, image_key(inst), inst.base_commit,
                       model=model, max_steps=steps, diary=diary)
    # A quota death before the first step says nothing about this instance.
    # Recording it would poison the checkpoint (measured: an entire Kimi n=25
    # campaign persisted as 25 empty rows during one 429 window). Raise so the
    # campaign treats it as weather.
    if r.steps == 0 and any(code in (r.error or "") for code in ("429", "404", "410")):
        raise RateLimited(r.error)
    transcripts.mkdir(parents=True, exist_ok=True)
    (transcripts / f"{inst.instance_id}.json").write_text(
        json.dumps(r.transcript, indent=1))
    row = dataclasses.asdict(r)
    row.pop("transcript")
    row["instance_id"] = inst.instance_id
    row["applied"] = bool(r.diff.strip()) and git_apply_check(
        tree_at(inst.repo, inst.base_commit), r.diff)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("--workers", type=int, default=2)
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
    ckpt_path, preds_path, graded_path = (OUT / "checkpoint.jsonl",
                                          OUT / "predictions.jsonl", OUT / "graded.json")
    transcripts = OUT / "transcripts"
    model_tag = f"fixpoint-shell-{args.model}"

    import fcntl
    lock_handle = (OUT / ".lock").open("w")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"another run is already active in {OUT} — refusing to interleave")
        return 1

    rows = {iid: r for iid, r in load_checkpoint(ckpt_path, args.steps).items()
            if iid in by_id}
    graded: dict[str, bool] = {}
    if graded_path.exists():
        try:
            data = json.loads(graded_path.read_text())
            if data.get("experiment") == {"steps": args.steps}:
                graded = {i: v for i, v in data.get("per_instance", {}).items() if i in by_id}
        except ValueError:
            pass  # torn file: verdicts regrade from harness logs
    if rows or graded:
        print(f"resuming — {len(rows)} generated, {len(graded)} graded", flush=True)

    chunks = plan_chunks([i.instance_id for i in instances], meta, max_chunk=args.chunk_size)
    print(f"shell bench: {len(instances)} instances in {len(chunks)} chunks, "
          f"steps={args.steps}, model={args.model}", flush=True)

    def persist() -> None:
        ordered = [rows[i.instance_id] for i in instances if i.instance_id in rows]
        atomic_write(preds_path, "".join(
            json.dumps({"instance_id": i.instance_id, "model_name_or_path": model_tag,
                        "model_patch": (rows.get(i.instance_id) or {}).get("diff", "")}) + "\n"
            for i in instances))
        empty = sum(1 for r in ordered if not (r.get("diff") or "").strip())
        atomic_write(graded_path, json.dumps({
            "model": model_tag, "total_predictions": len(instances), "empty": empty,
            "experiment": {"steps": args.steps},
            "graded": len(graded), "resolved": sum(graded.values()),
            "per_instance": graded}, indent=2))
        atomic_write(OUT / "results.json", json.dumps({
            "model": args.model, "mode": "shell", "n": len(instances),
            "steps_budget": args.steps,
            "submitted": sum(1 for r in ordered if r.get("submitted")),
            "total_cost_usd": round(sum(r.get("cost_usd", 0.0) for r in ordered), 4),
            "rows": ordered}, indent=2))

    started = time.time()
    base = f"shellbench-{int(started)}"
    for ci, chunk in enumerate(chunks, 1):
        todo = [iid for iid in chunk if iid not in rows]
        needs_grading = any(iid not in graded
                            and (rows.get(iid, {}).get("diff") or "").strip()
                            for iid in chunk)
        if not todo and not needs_grading:
            continue
        print(f"\nchunk {ci}/{len(chunks)} ({len(chunk)} instances, {len(todo)} to generate)",
              flush=True)
        pulled, failed = prepull(chunk, args.namespace)
        print(f"  pre-pulled {pulled}/{len(chunk)} images"
              + (f" ({failed} unavailable)" if failed else ""), flush=True)

        # Circuit breaker: consecutive rate-limit deaths mean the QUOTA is
        # gone, not the instances — pressing on just converts the whole
        # campaign into a failure log. Two strikes pauses everything for half
        # an hour; the instances stay in todo and retry on the next lap.
        remaining = list(todo)
        while remaining:
            batch, remaining = remaining, []
            rl_streak = 0
            with ThreadPoolExecutor(max_workers=args.workers) as pool, \
                    ckpt_path.open("a") as ckpt:
                futures = {pool.submit(run_one, by_id[iid], args.steps, args.model,
                                       transcripts): iid for iid in batch}
                try:
                    for fut in as_completed(futures):
                        iid = futures[fut]
                        try:
                            row = rows[iid] = fut.result()
                        except RateLimited as e:
                            rl_streak += 1
                            remaining.append(iid)
                            print(f"  ~ {iid}: rate-limited, will retry ({e})"[:160],
                                  flush=True)
                            continue
                        except Exception as e:
                            print(f"  ! {iid}: {type(e).__name__}: {e}", flush=True)
                            continue
                        rl_streak = 0
                        ckpt.write(json.dumps({"steps_budget": args.steps,
                                               "row": row}) + "\n")
                        ckpt.flush()
                        print(f"  {iid}: submitted={row['submitted']} steps={row['steps']} "
                              f"applied={row['applied']}", flush=True)
                except KeyboardInterrupt:
                    pool.shutdown(wait=False, cancel_futures=True)
                    persist()
                    raise
            if remaining:
                if rl_streak >= max(2, args.workers):
                    print(f"  quota wall ({rl_streak} consecutive) — pausing 30min "
                          f"with {len(remaining)} instances waiting", flush=True)
                    time.sleep(1800)
                else:
                    time.sleep(60)

        persist()
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
                    pass
            persist()
            print(f"  graded {got}/{len(to_grade)} — running total "
                  f"{sum(graded.values())} resolved / {len(graded)} graded", flush=True)
        prune_if_needed(args.prune_threshold_gb)

    ordered = [rows[i.instance_id] for i in instances if i.instance_id in rows]
    resolved = sum(graded.values())
    print(f"\nshell bench [{args.model}] — {time.time()-started:.0f}s this invocation")
    print(f"  generated  {len(ordered)}/{len(instances)}  "
          f"(submitted {sum(1 for r in ordered if r.get('submitted'))})")
    print(f"  RESOLVED   {resolved}/{len(instances)} = {resolved/len(instances):.1%}   "
          f"(official harness; graded {len(graded)})")
    print(f"  -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
