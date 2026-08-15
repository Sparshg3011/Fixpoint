#!/usr/bin/env python
"""Open a PR for a patch the agent produced. Dry-run by default.

    python scripts/open_pr.py --instance django__django-10914            # dry run
    python scripts/open_pr.py --instance django__django-10914 --execute  # really opens it

Safety: the PR is opened on YOUR fork, fix-branch against base-branch, so the
diff is exactly the agent's patch. Opening against an upstream project is
refused by fixpoint.pr.assert_safe_target.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fixpoint.bench import get, load_lite
from fixpoint.pr import PRSafetyError, open_pr

DEFAULT_PREDICTIONS = Path("data/singleshot/predictions.jsonl")
# Instances the official harness graded RESOLVED in the single-shot run — these
# are the ones worth showing off.
RESOLVED = {
    "astropy__astropy-14995", "django__django-10914", "django__django-14672",
    "matplotlib__matplotlib-24149", "pallets__flask-4992", "sphinx-doc__sphinx-8435",
    "sympy__sympy-13647", "sympy__sympy-15609", "sympy__sympy-18621",
}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--instance", required=True)
    p.add_argument("--predictions", default=str(DEFAULT_PREDICTIONS))
    p.add_argument("--execute", action="store_true",
                   help="actually open the PR (default is a dry run)")
    args = p.parse_args()

    preds = {json.loads(line)["instance_id"]: json.loads(line)
             for line in Path(args.predictions).read_text().splitlines() if line.strip()}
    if args.instance not in preds:
        print(f"{args.instance} not in {args.predictions}")
        return 1
    patch = preds[args.instance].get("model_patch", "")
    if not patch.strip():
        print(f"{args.instance} has an empty patch — nothing to open")
        return 1

    inst = get(load_lite(), args.instance)
    try:
        res = open_pr(upstream=inst.repo, base_commit=inst.base_commit, patch=patch,
                      instance_id=inst.instance_id,
                      problem_statement=inst.problem_statement,
                      resolved=args.instance in RESOLVED,
                      dry_run=not args.execute)
    except PRSafetyError as e:
        print(f"SAFETY REFUSAL: {e}")
        return 1

    print(f"instance : {args.instance}  (graded resolved: {args.instance in RESOLVED})")
    print(f"branches : {res.branch} -> {res.base_branch}")
    print(res.url)
    if res.dry_run:
        print("\n(dry run — re-run with --execute to actually open the PR)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
