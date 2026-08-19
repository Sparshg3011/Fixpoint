#!/usr/bin/env python
"""Measure the keep-first-diff loop policy retroactively — no API calls.

The loop-100 campaign kept the LATEST attempt's diff and scored 41/100, three
below same-scaffold single-shot on the same instances. The keep-first policy
(loop.py, pinned by test) claims those points back — but a policy change is a
hypothesis until graded. Rerunning the whole campaign costs ~30h of API time;
this doesn't: every attempt's diff is already in the run diaries, so we can
swap in each instance's FIRST applying diff, regrade only the instances where
that actually changes the patch, and read the answer off the official harness.

    python scripts/regrade_keepfirst.py --dir data/loop/<model> [--grade]

Without --grade it reports how many instances change. With --grade it builds
a keepfirst/ sub-directory (predictions + verdicts) and grades the changed
instances; verdicts for unchanged instances carry over untouched.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from grade_chunked import prepull  # noqa: E402

from fixpoint.bench import load_lite  # noqa: E402
from fixpoint.diary import RUNS_DIR, read  # noqa: E402
from fixpoint.eval.chunking import plan_chunks  # noqa: E402
from fixpoint.harness import read_instance_report, run_official_eval, summarize_report  # noqa: E402


def first_attempt_diff(instance_id: str) -> str | None:
    """The first developer-succeeded diff from the instance's newest diary."""
    diaries = sorted(RUNS_DIR.glob(f"{instance_id}-*.jsonl"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    for path in diaries:
        for e in read(path):
            if e.stage == "developer" and e.event == "succeeded":
                return e.detail.get("diff") or None
        break  # only the newest diary describes the campaign row
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--grade", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    d = Path(args.dir)
    res = json.loads((d / "results.json").read_text())
    graded = json.loads((d / "graded.json").read_text())["per_instance"]
    rows = {r["instance_id"]: r for r in res["rows"]}

    changed: dict[str, str] = {}
    for iid, row in rows.items():
        # Green rows earned their diff; single-attempt rows have nothing to swap.
        if row.get("loop_green") or row.get("attempts", 1) < 2:
            continue
        first = first_attempt_diff(iid)
        if first and first != row.get("diff"):
            changed[iid] = first

    keep = {iid: v for iid, v in graded.items() if iid not in changed}
    print(f"{len(rows)} rows; {len(changed)} change diff under keep-first; "
          f"{len(keep)} verdicts carry over (baseline resolved: "
          f"{sum(graded.values())}/{len(rows)})")
    for iid in sorted(changed):
        print(f"  regrade: {iid} (was {'resolved' if graded.get(iid) else 'unresolved'})")
    if not args.grade:
        print("\ndry run — pass --grade to grade the changed instances")
        return 0

    out = d / "keepfirst"
    out.mkdir(exist_ok=True)
    tag = json.loads((d / "predictions.jsonl").read_text().splitlines()[0])["model_name_or_path"]
    preds = out / "predictions.jsonl"
    with preds.open("w") as f:
        for iid, diff in sorted(changed.items()):
            f.write(json.dumps({"instance_id": iid, "model_name_or_path": tag,
                                "model_patch": diff}) + "\n")

    meta = {i.instance_id: (i.repo, i.version) for i in load_lite()}
    verdicts: dict[str, bool] = {}
    base = f"keepfirst-{int(time.time())}"
    for ci, chunk in enumerate(plan_chunks(sorted(changed), meta, max_chunk=15), 1):
        run_id = f"{base}-c{ci}"
        prepull(chunk)
        try:
            run_official_eval(preds, run_id=run_id, instance_ids=chunk, max_workers=args.workers)
        except subprocess.CalledProcessError as e:
            print(f"  harness exited nonzero ({e.returncode}) — reading what it wrote", flush=True)
        for iid in chunk:
            try:
                verdicts[iid] = bool(summarize_report(
                    read_instance_report(run_id, tag, iid))["resolved"])
            except FileNotFoundError:
                pass
        print(f"  chunk {ci}: running keep-first verdicts {sum(verdicts.values())}"
              f"/{len(verdicts)}", flush=True)

    final = {**keep, **verdicts}
    (out / "graded.json").write_text(json.dumps({
        "model": tag, "policy": "keep-first-diff", "baseline_resolved": sum(graded.values()),
        "total_predictions": len(rows), "graded": len(final),
        "resolved": sum(final.values()), "per_instance": final}, indent=2))
    print(f"\nkeep-first policy: {sum(final.values())}/{len(rows)} resolved "
          f"(latest-diff baseline: {sum(graded.values())}/{len(rows)})")
    print(f"-> {out / 'graded.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
