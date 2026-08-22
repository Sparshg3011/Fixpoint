#!/usr/bin/env python
"""Grade a large predictions file in chunks, pruning Docker images between them.

Why this exists: grading Lite-300 needs ~90 distinct instance images at 3-4GB
each — far more than Docker Desktop's VM disk holds. A single flat run fills the
VM partway through and every remaining instance fails with a misleading
"no matching manifest" error that is really "no space left on device". Chunking
plus a prune between chunks keeps the working set bounded.

Resumable by design: results are written per chunk, so an interrupted run picks
up where it stopped instead of re-grading hours of work.

    python scripts/grade_chunked.py --predictions data/singleshot/<model>/predictions.jsonl \\
        [--chunk-size 30] [--workers 4]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fixpoint.bench import load_lite
from fixpoint.eval.chunking import DEFAULT_PRUNE_THRESHOLD_GB, parse_docker_size, plan_chunks
from fixpoint.harness import read_instance_report, run_official_eval, summarize_report


def images_gb() -> float:
    """Total size of the Docker image store, in GB (0.0 if unreadable)."""
    r = subprocess.run(["docker", "system", "df", "--format", "{{.Type}}\t{{.Size}}"],
                       capture_output=True, text=True)
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[0].strip() == "Images":
            return parse_docker_size(parts[1])
    return 0.0


def prune_if_needed(threshold_gb: float) -> bool:
    """Prune the image store only when it crosses the threshold.

    Pruning after every chunk (the first implementation) constantly re-downloads
    the base and env layers that adjacent chunks share; with chunks ordered by
    (repo, version) those layers are exactly what we want to KEEP until disk
    pressure actually demands otherwise."""
    used = images_gb()
    if used < threshold_gb:
        return False
    subprocess.run(["docker", "image", "prune", "-a", "-f"], capture_output=True)
    print(f"  pruned image store ({used:.0f}GB > {threshold_gb:.0f}GB threshold)", flush=True)
    return True


def prepull(instance_ids: list[str], namespace: str = "swebench",
            instances=None) -> tuple[int, int]:
    """Pull each instance image explicitly as linux/amd64 before grading.

    THE fix for Apple Silicon. These images publish an amd64-only manifest, and
    the harness pulls without specifying a platform — so on an arm64 host Docker
    refuses with "no matching manifest", which the harness reports as
    "Error building image". That cost ~40% of instances in the first attempt.
    Pulling explicitly here puts the image in the local store, and the harness
    then finds it instead of trying to fetch it itself.

    Returns (pulled_ok, failed).
    """
    from fixpoint.bench import get, load_lite
    from fixpoint.eval.images import image_key

    insts = instances if instances is not None else load_lite()
    ok = bad = 0
    for iid in instance_ids:
        img = image_key(get(insts, iid), namespace=namespace)
        # --quiet keeps the layer chatter out of the log; failures still surface
        # via the return code, and a failed pull simply means this instance
        # cannot be graded (recorded as ungraded, never as unresolved).
        r = subprocess.run(["docker", "pull", "--platform", "linux/amd64", "--quiet", img],
                           capture_output=True, text=True)
        if r.returncode == 0:
            ok += 1
        else:
            bad += 1
    return ok, bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--predictions", required=True)
    ap.add_argument("--chunk-size", type=int, default=30,
                    help="instances per chunk; keep the image working set under the VM disk")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--namespace", default="swebench", choices=["swebench", "none"])
    ap.add_argument("--prune-threshold-gb", type=float, default=DEFAULT_PRUNE_THRESHOLD_GB,
                    help="prune docker images only when the store exceeds this")
    args = ap.parse_args()

    preds_path = Path(args.predictions)
    preds = [json.loads(ln) for ln in preds_path.read_text().splitlines() if ln.strip()]
    model_name = preds[0]["model_name_or_path"]
    total = len(preds)
    with_patch = [p["instance_id"] for p in preds if p.get("model_patch", "").strip()]
    empty = total - len(with_patch)

    base = args.run_id or f"chunked-{int(time.time())}"
    out = preds_path.parent / "graded.json"
    done: dict[str, bool] = {}
    if out.exists():  # resume
        done = {k: v for k, v in json.loads(out.read_text()).get("per_instance", {}).items()}
        print(f"resuming — {len(done)} instances already graded")

    todo = [i for i in with_patch if i not in done]
    # Chunks grouped by (repo, version): each env layer downloads once, not
    # once per chunk it would otherwise straddle.
    meta = {i.instance_id: (i.repo, i.version) for i in load_lite()}
    chunks = plan_chunks(todo, meta, max_chunk=args.chunk_size)
    print(f"{total} predictions ({empty} empty) — grading {len(todo)} in {len(chunks)} chunks "
          f"of <={args.chunk_size}, {args.workers} workers\n")

    started = time.time()
    for ci, chunk in enumerate(chunks, 1):
        run_id = f"{base}-c{ci}"
        t0 = time.time()
        print(f"chunk {ci}/{len(chunks)} ({len(chunk)} instances) …", flush=True)
        pulled, failed_pull = prepull(chunk, args.namespace)
        print(f"  pre-pulled {pulled}/{len(chunk)} images"
              + (f" ({failed_pull} unavailable)" if failed_pull else ""), flush=True)
        try:
            run_official_eval(preds_path, run_id=run_id, instance_ids=chunk,
                              namespace=args.namespace, max_workers=args.workers)
        except subprocess.CalledProcessError as e:
            print(f"  harness exited nonzero ({e.returncode}) — reading whatever it wrote")

        got = 0
        for iid in chunk:
            try:
                done[iid] = bool(summarize_report(read_instance_report(run_id, model_name, iid))["resolved"])
                got += 1
            except FileNotFoundError:
                pass  # image/env failure — counted as ungraded, not as unresolved
        resolved = sum(done.values())
        print(f"  graded {got}/{len(chunk)} in {time.time()-t0:.0f}s — "
              f"running total: {resolved} resolved / {len(done)} graded")

        # Persist after every chunk so an interruption never loses hours.
        out.write_text(json.dumps({
            "model": model_name, "total_predictions": total, "empty": empty,
            "graded": len(done), "resolved": sum(done.values()),
            "per_instance": done,
        }, indent=2))
        prune_if_needed(args.prune_threshold_gb)

    graded, resolved = len(done), sum(done.values())
    print(f"\n{model_name} — {time.time()-started:.0f}s total")
    print(f"  RESOLVED {resolved}/{total} = {resolved/total:.1%}   "
          f"(graded {graded}, empty {empty}, ungraded {total-graded-empty})")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
