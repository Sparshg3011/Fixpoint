"""Rescue rows lost to sanitizer bugs — offline, from saved raw responses.

Every single-shot row keeps the model's raw output precisely so that a parser
fix can be applied retroactively without burning API calls. The GLM Lite-300
run predates the marker-canonicalization hardening (the 49b investigation):
its responses contain valid SEARCH/REPLACE blocks the old parser rejected.
Those failures were OURS, not the model's — re-running the current sanitizer
over the same responses corrects the measurement, it does not give the model
another try.

Scope is deliberately narrow: only rows whose diff is empty are touched, and
they go through the exact shipped pipeline semantics — edits must target files
that were actually shown to the model (top_files), contents come from the same
base-commit checkout, applied means the same real `git apply --check`. The
guided-retrieval re-ask cannot be replayed offline, so rows that needed it
stay failed.

    python scripts/rescue_parse.py --dir data/singleshot/z-ai_glm-5.2          # dry run
    python scripts/rescue_parse.py --dir data/singleshot/z-ai_glm-5.2 --write

--write archives the originals to pre-rescue-archive/ first, then rewrites
results.json and predictions.jsonl. graded.json is left alone: existing
verdicts stay valid, and grade_chunked.py resumes from it to grade only the
newly non-empty instances.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fixpoint.agent.edits import EditApplyError, parse_edits, synthesize_diff  # noqa: E402
from fixpoint.bench.loader import load_lite  # noqa: E402
from fixpoint.eval.singleshot import git_apply_check  # noqa: E402
from fixpoint.retrieval import tree_at  # noqa: E402


def rescue_row(row: dict, inst, full_tree: bool = False) -> tuple[str, str | None]:
    """One row -> (status, diff). Statuses mirror the pipeline's real failure
    modes so the summary reads like the original error breakdown."""
    edits = parse_edits(row.get("raw_response") or "")
    if not edits:
        return "no-edit-blocks", None

    tree = tree_at(inst.repo, inst.base_commit)
    # Same contract as the run: the model may only edit files it was shown.
    files = {}
    for p in row.get("top_files", []):
        f = tree / p
        if f.is_file():
            files[p] = f.read_text(errors="replace")
    if full_tree:
        # Measurement mode: also honor edits to real files the model was never
        # shown. It wrote SEARCH text from memory of the codebase; if that text
        # matches the actual file, the edit was valid and only our shown-files
        # restriction rejected it. SEARCH that doesn't match still fails.
        for e in edits:
            f = tree / e.path
            if e.path not in files and ".." not in e.path and f.is_file():
                files[e.path] = f.read_text(errors="replace")

    try:
        diff = synthesize_diff(files, edits)
    except EditApplyError as e:
        msg = str(e)
        return ("unprovided-file" if "not among the provided" in msg
                else "search-not-found"), None
    if not diff.strip():
        return "no-net-change", None
    if not git_apply_check(tree, diff):
        return "apply-check-failed", None
    return "rescued", diff


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, help="model dir under data/singleshot")
    ap.add_argument("--write", action="store_true",
                    help="persist rescued rows (default: report only)")
    ap.add_argument("--full-tree", action="store_true",
                    help="also honor edits to real repo files the model was not shown")
    args = ap.parse_args()

    d = Path(args.dir)
    results = json.loads((d / "results.json").read_text())
    by_id = {i.instance_id: i for i in load_lite()}

    statuses: Counter[str] = Counter()
    rescued = 0
    for row in results["rows"]:
        if (row.get("diff") or "").strip():
            continue  # only ever touch rows the old parser left empty
        inst = by_id.get(row["instance_id"])
        if inst is None:
            statuses["unknown-instance"] += 1
            continue
        status, diff = rescue_row(row, inst, full_tree=args.full_tree)
        statuses[status] += 1
        if status == "rescued":
            rescued += 1
            row.update(diff=diff, applied=True, error=None, rescued=True)
            print(f"  rescued  {row['instance_id']}")

    print(f"\n{d.name}: {rescued} rescued")
    for status, count in statuses.most_common():
        print(f"  {count:>3}  {status}")

    if not args.write:
        print("\ndry run — pass --write to persist")
        return 0
    if not rescued:
        print("nothing to write")
        return 0

    # Originals first, so the pre-rescue measurement stays inspectable forever.
    archive = d / "pre-rescue-archive"
    archive.mkdir(exist_ok=True)
    for name in ("results.json", "predictions.jsonl"):
        if (d / name).exists() and not (archive / name).exists():
            shutil.copy2(d / name, archive / name)

    rows = results["rows"]
    applied = sum(1 for r in rows if r.get("applied"))
    results["apply_rate"] = applied / len(rows)
    (d / "results.json").write_text(json.dumps(results, indent=2))
    with (d / "predictions.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps({
                "instance_id": r["instance_id"],
                "model_name_or_path": results["model"],
                "model_patch": r.get("diff") or "",
            }) + "\n")
    print(f"\nwrote {d/'results.json'} and {d/'predictions.jsonl'} "
          f"(originals in {archive}/)")
    print(f"next: python scripts/grade_chunked.py --predictions {d/'predictions.jsonl'} "
          f"--run-id rescue")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
