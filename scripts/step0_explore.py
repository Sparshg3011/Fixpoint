"""Step 0 plumbing: download SWE-bench_Lite, fingerprint it, dump one instance for hand-tracing.

This script is deliberately dumb: no analysis lives here. Analysis is bench/stats.py (you write that).

WHY a fingerprint: "300 tasks" is not a reproducible claim. The dataset is a moving target only if
we let it be — pinning a hash over (instance_id, base_commit) pins BOTH the task list AND the exact
code states we'll be graded against. Every number we ever report cites this hash.
"""

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from datasets import load_dataset

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "data" / "step0"

# The instance we hand-trace in session 0. Small, famous, crystal-clear issue text.
TRACE_ID = "django__django-11099"


def main() -> None:
    ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")

    print(f"n_instances = {len(ds)}")
    print(f"columns     = {ds.column_names}\n")

    # --- per-repo distribution ---------------------------------------------
    by_repo = Counter(ds["repo"])
    print(f"{'repo':<28} {'n':>4}")
    for repo, n in by_repo.most_common():
        print(f"{repo:<28} {n:>4}")

    # --- fingerprint --------------------------------------------------------
    # sorted() so the hash is independent of row order; instance_id:base_commit
    # because those two fields jointly define the task set.
    lines = sorted(f"{r['instance_id']}:{r['base_commit']}" for r in ds)
    fp = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    print(f"\nfingerprint sha256(instance_id:base_commit, sorted) = {fp}")

    # --- dump one instance for the hand-trace -------------------------------
    row = next((r for r in ds if r["instance_id"] == TRACE_ID), None)
    if row is None:
        print(f"\n{TRACE_ID} not in Lite; falling back to first instance", file=sys.stderr)
        row = ds[0]

    inst_dir = OUT_DIR / row["instance_id"]
    inst_dir.mkdir(parents=True, exist_ok=True)
    (inst_dir / "problem_statement.md").write_text(row["problem_statement"])
    (inst_dir / "gold_patch.diff").write_text(row["patch"])
    (inst_dir / "test_patch.diff").write_text(row["test_patch"])
    meta = {
        k: row[k]
        for k in ("instance_id", "repo", "base_commit", "environment_setup_commit",
                  "version", "created_at", "FAIL_TO_PASS", "PASS_TO_PASS")
    }
    (inst_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    print(f"\n--- {row['instance_id']} (dumped to {inst_dir.relative_to(REPO_ROOT)}) ---")
    for k in ds.column_names:
        v = str(row[k])
        flat = v.replace("\n", "\\n")
        print(f"{k:<26} len={len(v):>6}  {flat[:100]}")


if __name__ == "__main__":
    main()
