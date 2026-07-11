#!/usr/bin/env python
"""Red/green calibration through the official harness.

    RED    a do-nothing patch must leave each bug in place — FAIL_TO_PASS
           tests fail, verdict unresolved. Proves the harness sees broken-ness.
    GREEN  the human gold patch must grade RESOLVED. Proves env build + patch
           apply + test scoping + log parsing work end to end on this machine.

A red run also exposes mined-label noise: any FAIL_TO_PASS test that passes
with no fix applied was flaky or coupled at dataset-construction time. Those
get printed per instance, never averaged away.

Usage:
    python scripts/calibrate.py [instance_id]         # one instance
    python scripts/calibrate.py --subset 25           # deterministic subset
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

# scripts/ is not a package; make the repo importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import swebench

from fixpoint.bench import Instance, get, load_lite
from fixpoint.eval import lite_subset
from fixpoint.harness import (
    CALIB_DIR,
    NOOP_PATCH,
    read_instance_report,
    run_official_eval,
    summarize_report,
    write_predictions,
)

# Our session-0 trace instance: tiny gold patch, crisp issue, one known F2P
# anomaly — ideal for single-instance calibration.
DEFAULT_INSTANCE = "django__django-11099"


def collect(run_id: str, model_name: str, instances: list[Instance]) -> dict[str, dict]:
    """Read per-instance reports; a missing report is an env/harness error,
    which for a subset run is a finding to record, not a reason to crash."""
    out: dict[str, dict] = {}
    for inst in instances:
        try:
            out[inst.instance_id] = summarize_report(
                read_instance_report(run_id, model_name, inst.instance_id)
            )
        except FileNotFoundError:
            out[inst.instance_id] = {"error": "no report — env build or harness failure"}
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance_id", nargs="?", default=DEFAULT_INSTANCE)
    parser.add_argument("--subset", type=int, default=None,
                        help="calibrate the deterministic n-instance subset instead")
    parser.add_argument("--namespace", default="swebench", choices=["swebench", "none"],
                        help="swebench = pull prebuilt images; none = build locally")
    parser.add_argument("--max-workers", type=int, default=None)
    args = parser.parse_args()

    instances = load_lite()
    if args.subset:
        chosen = lite_subset(instances, args.subset)
        tag = f"s{args.subset}"
    else:
        chosen = [get(instances, args.instance_id)]
        tag = chosen[0].instance_id
    ids = [i.instance_id for i in chosen]
    workers = args.max_workers or (3 if len(chosen) > 1 else 1)
    print(f"calibrating {len(ids)} instance(s) [{tag}] with {workers} workers")

    started = time.time()
    preds = write_predictions(CALIB_DIR / f"noop-{tag}.jsonl", "fixpoint-noop",
                              {i: NOOP_PATCH for i in ids})
    run_official_eval(preds, run_id=f"calib-red-{tag}", instance_ids=ids,
                      namespace=args.namespace, max_workers=workers)
    red = collect(f"calib-red-{tag}", "fixpoint-noop", chosen)

    run_official_eval("gold", run_id=f"calib-green-{tag}", instance_ids=ids,
                      namespace=args.namespace, max_workers=workers)
    green = collect(f"calib-green-{tag}", "gold", chosen)
    wall = round(time.time() - started, 1)

    # ---- verdicts -----------------------------------------------------------
    # Red must NOT require every F2P test to fail (mined noise is expected);
    # it must apply, stay unresolved, and have at least one truly failing F2P.
    rows, failures, noise = [], [], {}
    for inst in chosen:
        r, g = red[inst.instance_id], green[inst.instance_id]
        if "error" in r or "error" in g:
            gate = "ERROR"
        else:
            ok_red = r["applied"] and not r["resolved"] and r["f2p_fail"] >= 1
            ok_green = g["applied"] and g["resolved"]
            gate = "PASS" if (ok_red and ok_green) else "FAIL"
            anomalies = sorted(set(inst.fail_to_pass) - set(r["f2p_failing_tests"]))
            if anomalies:
                noise[inst.instance_id] = anomalies
        if gate != "PASS":
            failures.append(inst.instance_id)
        rows.append((inst.instance_id, r, g, gate))

    print(f"\ncalibration [{tag}] — {platform.machine()}, swebench {swebench.__version__}, {wall}s total")
    print(f"{'instance':<42}{'red':<12}{'green':<12}{'gate':<8}{'f2p noise'}")
    for iid, r, g, gate in rows:
        red_s = "error" if "error" in r else ("unresolved" if not r["resolved"] else "RESOLVED!")
        green_s = "error" if "error" in g else ("resolved" if g["resolved"] else "UNRESOLVED")
        print(f"{iid:<42}{red_s:<12}{green_s:<12}{gate:<8}{len(noise.get(iid, []))}")
    if noise:
        print("\nF2P tests passing with NO fix applied (mined-label noise):")
        for iid, tests in noise.items():
            for t in tests:
                print(f"  {iid}: {t}")

    out = CALIB_DIR / f"calibration-{tag}.json"
    out.write_text(json.dumps({
        "tag": tag, "machine": platform.machine(),
        "swebench_version": swebench.__version__,
        "namespace": args.namespace, "workers": workers, "wall_s": wall,
        "red": red, "green": green,
        "f2p_noise": noise, "failures": failures,
    }, indent=2))
    print(f"\nsummary -> {out}")
    print(f"gate: {'PASS' if not failures else f'FAIL ({len(failures)}/{len(ids)} instances)'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
