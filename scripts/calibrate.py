#!/usr/bin/env python
"""Step-1 gate: the two runs that make every future number trustworthy.

    RED    a do-nothing patch must leave the bug in place — FAIL_TO_PASS tests
           fail, verdict unresolved. Proves the harness can see broken-ness.
    GREEN  the human gold patch must grade RESOLVED — all F2P pass, all P2P
           pass. Proves env build + patch apply + test scoping + log parsing
           work end to end on this machine.

If either half fails, nothing built downstream can be trusted, so this script
exits nonzero and the plan says stop and fix.

Usage:
    python scripts/calibrate.py [instance_id] [--namespace swebench|none]

The red run also answers an open question from our session-0 trace: any
FAIL_TO_PASS test that *passes* under the no-op patch was flaky or coupled at
dataset-mining time (F2P sets are mined from two executions, never authored).
We print those anomalies instead of averaging them away.
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

from fixpoint.bench import get, load_lite
from fixpoint.harness import (
    CALIB_DIR,
    NOOP_PATCH,
    read_instance_report,
    run_official_eval,
    summarize_report,
    write_predictions,
)

# Our session-0 trace instance: tiny gold patch, crisp issue, and one known
# oddity in its F2P list — ideal for calibrating both the harness and us.
DEFAULT_INSTANCE = "django__django-11099"


def run_half(name: str, predictions: str | Path, model_name: str, instance_id: str, namespace: str) -> dict:
    """One calibration half: evaluate, read the report, time it."""
    started = time.time()
    print(f"[{name}] harness starting (namespace={namespace}) — log: {CALIB_DIR / f'calib-{name}.log'}")
    run_official_eval(predictions, run_id=f"calib-{name}", instance_ids=[instance_id], namespace=namespace)
    summary = summarize_report(read_instance_report(f"calib-{name}", model_name, instance_id))
    summary["wall_s"] = round(time.time() - started, 1)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance_id", nargs="?", default=DEFAULT_INSTANCE)
    parser.add_argument("--namespace", default="swebench", choices=["swebench", "none"],
                        help="swebench = pull prebuilt images; none = build locally")
    args = parser.parse_args()

    inst = get(load_lite(), args.instance_id)

    # RED: a syntactically-valid patch that fixes nothing (see NOOP_PATCH for
    # why a literally empty patch would be silently skipped, proving nothing).
    preds = write_predictions(CALIB_DIR / "noop_predictions.jsonl", "fixpoint-noop",
                              {inst.instance_id: NOOP_PATCH})
    red = run_half("red", preds, "fixpoint-noop", inst.instance_id, args.namespace)

    # GREEN: `-p gold` makes the harness grade the dataset's own human patch.
    green = run_half("green", "gold", "gold", inst.instance_id, args.namespace)

    # ---- verdicts -----------------------------------------------------------
    # Red must NOT require every F2P test to fail: a mined F2P entry that
    # passes pre-fix is dataset noise, and we want it surfaced, not hidden.
    gate_red = red["applied"] and not red["resolved"] and red["f2p_fail"] >= 1
    gate_green = green["applied"] and green["resolved"]
    anomalies = sorted(set(inst.fail_to_pass) - set(red["f2p_failing_tests"]))

    print(f"\ncalibration — {inst.instance_id} ({platform.machine()}, swebench {swebench.__version__})")
    print(f"{'run':<7}{'applied':<9}{'F2P pass':<10}{'F2P fail':<10}{'P2P pass':<10}{'P2P fail':<10}{'resolved':<10}{'wall'}")
    for name, s in (("red", red), ("green", green)):
        print(f"{name:<7}{str(s['applied']):<9}{s['f2p_pass']:<10}{s['f2p_fail']:<10}"
              f"{s['p2p_pass']:<10}{s['p2p_fail']:<10}{str(s['resolved']):<10}{s['wall_s']}s")
    if anomalies:
        print("\nF2P tests that already pass with NO fix applied (mined-label noise):")
        for t in anomalies:
            print(f"  - {t}")

    # Machine-readable summary so the README can cite calibration verbatim.
    out = CALIB_DIR / f"{inst.instance_id}.json"
    out.write_text(json.dumps({
        "instance_id": inst.instance_id,
        "machine": platform.machine(),
        "swebench_version": swebench.__version__,
        "namespace": args.namespace,
        "red": red,
        "green": green,
        "f2p_anomalies_in_red": anomalies,
        "gate": {"red": gate_red, "green": gate_green},
    }, indent=2))
    print(f"\nsummary -> {out.relative_to(Path.cwd()) if out.is_relative_to(Path.cwd()) else out}")

    print(f"\ngate: {'PASS' if gate_red and gate_green else 'FAIL'} "
          f"(red={'ok' if gate_red else 'FAIL'}, green={'ok' if gate_green else 'FAIL'})")
    return 0 if (gate_red and gate_green) else 1


if __name__ == "__main__":
    raise SystemExit(main())
