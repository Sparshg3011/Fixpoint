"""Thin wrapper around the official SWE-bench evaluation harness.

We shell out to `python -m swebench.harness.run_evaluation` rather than import
its internals: the harness is CLI-first (it forks workers and owns its logging),
and shelling out keeps our grading invocation byte-identical to what anyone
else would run to reproduce our numbers.

What the harness actually does per instance — read from the swebench 4.1.0
source, not from docs, so none of this is folklore:

  1. Builds or pulls a 3-layer Docker image: base (OS + conda) -> env (repo
     deps pinned per repo+version) -> instance (repo checked out at
     base_commit).
  2. Applies the model patch inside the container, trying in order:
         git apply --verbose
         git apply --verbose --reject          <- partial application allowed!
         patch --batch --fuzz=5 -p1 -i         (run_evaluation.py:65-67)
     If all three fail the instance grades unresolved — a malformed diff costs
     exactly as much as a wrong one. This chain is the spec our step-3
     sanitizer is written against.
  3. Runs an eval script that FIRST resets any test files the grading patch
     touches (`git checkout <base_commit> <test_files>`), THEN applies
     test_patch, then runs the repo-specific scoped test command
     (test_spec/python.py:414). Consequence: model edits to graded test files
     are silently wiped — the tests cannot be gamed, and test-file hunks in a
     model patch are dead weight.
  4. Parses the test log with a per-repo parser into {test name: status} and
     grades: resolved iff every FAIL_TO_PASS passes and every PASS_TO_PASS
     passes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fixpoint.bench import DATASET_NAME

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Harness output lands under logs/run_evaluation/<run_id>/<model>/<instance_id>/.
# Gitignored: logs are evidence for a specific machine, not source.
LOGS_DIR = REPO_ROOT / "logs" / "run_evaluation"

# Calibration artifacts (predictions files, roll-up reports, summaries).
CALIB_DIR = REPO_ROOT / "data" / "calibration"

# A patch that applies cleanly anywhere and changes nothing any test imports:
# it creates one brand-new file, so it needs zero surrounding context. Purpose:
# force a full RED evaluation. A genuinely empty patch would not do — the
# harness filters empty predictions out before any container starts, which
# would calibrate nothing.
NOOP_PATCH = """\
diff --git a/.fixpoint_noop b/.fixpoint_noop
new file mode 100644
--- /dev/null
+++ b/.fixpoint_noop
@@ -0,0 +1 @@
+calibration no-op: applies cleanly, touches nothing the tests can see
"""


def write_predictions(path: Path, model_name: str, patches: dict[str, str]) -> Path:
    """Write predictions in the harness's input format.

    One JSON object per line with exactly these keys — model_name_or_path also
    becomes the on-disk report directory name, so keep it filesystem-safe.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for instance_id, patch in patches.items():
            f.write(
                json.dumps(
                    {
                        "instance_id": instance_id,
                        "model_name_or_path": model_name,
                        "model_patch": patch,
                    }
                )
                + "\n"
            )
    return path


def run_official_eval(
    predictions_path: str | Path,
    run_id: str,
    instance_ids: list[str],
    *,
    dataset: str = DATASET_NAME,
    namespace: str = "swebench",
    timeout_s: int = 1800,
    max_workers: int = 1,
    log_path: Path | None = None,
) -> None:
    """Invoke the official harness and stream its output to a log file.

    namespace="swebench" pulls prebuilt images from Docker Hub (fast when they
    exist for this CPU arch); namespace="none" builds all three image layers
    locally from spec (slow the first time — tens of minutes — but works
    anywhere). Callers fall back from the former to the latter.
    """
    CALIB_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name", dataset,
        "--split", "test",
        "--predictions_path", str(predictions_path),
        "--instance_ids", *instance_ids,
        "--run_id", run_id,
        "--max_workers", str(max_workers),
        "--timeout", str(timeout_s),
        "--namespace", namespace,
        "--report_dir", str(CALIB_DIR),
        # Keep instance images between the red and green runs — the default
        # ("env") deletes them after each run, which would force a rebuild/pull
        # in the middle of a single calibration.
        "--cache_level", "instance",
    ]
    log_path = log_path or (CALIB_DIR / f"{run_id}.log")
    with log_path.open("w") as log:
        # check=True: a harness crash must halt calibration loudly, not let us
        # read a stale report from a previous run and call it a result.
        subprocess.run(cmd, cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    # swebench 4.1.0 accepts --report_dir but still writes its roll-up report
    # (<model>.<run_id>.json) to CWD. Relocate so machine-generated evidence
    # never litters the repo root; if a future version honors the flag, the
    # glob matches nothing and this is a no-op.
    for rollup in REPO_ROOT.glob(f"*.{run_id}.json"):
        rollup.replace(CALIB_DIR / rollup.name)


def safe_model_name(model_name: str) -> str:
    """The on-disk form of a model name.

    The harness uses model_name_or_path as a DIRECTORY name, so an id like
    "z-ai/glm-5.2" would nest a directory. It rewrites "/" to "__"; we must
    apply the same rewrite when reading, or every report lookup misses and a
    perfectly good evaluation reads back as "errored" (which is exactly what
    happened once).
    """
    return model_name.replace("/", "__")


def read_instance_report(run_id: str, model_name: str, instance_id: str) -> dict:
    """Read the per-instance report.json the harness wrote for this run."""
    report_path = LOGS_DIR / run_id / safe_model_name(model_name) / instance_id / "report.json"
    data = json.loads(report_path.read_text())
    # The per-instance file nests everything under the instance id.
    return data.get(instance_id, data)


def summarize_report(report: dict) -> dict:
    """Flatten a harness report into the six numbers calibration cares about."""
    tests = report.get("tests_status", {})
    f2p = tests.get("FAIL_TO_PASS", {})
    p2p = tests.get("PASS_TO_PASS", {})
    return {
        "applied": bool(report.get("patch_successfully_applied", False)),
        "resolved": bool(report.get("resolved", False)),
        "f2p_pass": len(f2p.get("success", [])),
        "f2p_fail": len(f2p.get("failure", [])),
        "p2p_pass": len(p2p.get("success", [])),
        "p2p_fail": len(p2p.get("failure", [])),
        # Kept verbatim: which specific F2P tests failed is the interesting
        # part of a red run (it exposes mined-label noise — see calibrate.py).
        "f2p_failing_tests": list(f2p.get("failure", [])),
    }
