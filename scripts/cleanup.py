#!/usr/bin/env python
"""Reclaim the disk the benchmark used, keeping only the small evidence files.

Everything the benchmark produces is either regenerable machinery or small
JSON evidence. This script deletes the former and keeps the latter, so after a
full Lite-300 campaign the project's footprint drops from hundreds of GB to a
few hundred MB.

    python scripts/cleanup.py            # dry run: show what would go and why
    python scripts/cleanup.py --execute  # actually delete
    python scripts/cleanup.py --execute --deep   # also drop repo mirrors + HF cache

Deleted (regenerable):
  docker images        the big one — instance images re-pull on demand
  data/repos/trees     extracted checkouts; regenerate via `git archive`
  logs/**/test_output.txt and image-build logs (per-instance harness noise)
  data/repos/bare, ~/.cache/huggingface   only with --deep (re-cloned/re-fetched)

Kept (evidence — everything a reader needs to trust the numbers):
  data/**/graded.json, results.json, predictions.jsonl
  logs/**/report.json and patch.diff        (per-instance verdicts + patches)
  runs/ diaries, data/calibration/*.json, docs/

Docker Desktop note: deleting images frees space INSIDE the VM immediately;
the VM's disk file on the host shrinks via Docker Desktop itself (Settings ->
Resources, lower the virtual disk limit, Apply) — that last step is a GUI
action this script cannot perform.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO_ROOT = Path(__file__).resolve().parent.parent


def du_gb(path: Path) -> float:
    """Directory size in GB (0 if absent). du -sk is portable on macOS."""
    if not path.exists():
        return 0.0
    r = subprocess.run(["du", "-sk", str(path)], capture_output=True, text=True)
    try:
        return int(r.stdout.split()[0]) / 1024 / 1024
    except (ValueError, IndexError):
        return 0.0


def docker_images_gb() -> float:
    from fixpoint.eval.chunking import parse_docker_size
    r = subprocess.run(["docker", "system", "df", "--format", "{{.Type}}\t{{.Size}}"],
                       capture_output=True, text=True)
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[0].strip() == "Images":
            return parse_docker_size(parts[1])
    return 0.0


def harness_noise_files() -> list[Path]:
    """Large per-instance harness logs; report.json + patch.diff are KEPT."""
    logs = REPO_ROOT / "logs"
    if not logs.exists():
        return []
    keep = {"report.json", "patch.diff"}
    return [p for p in logs.rglob("*") if p.is_file() and p.name not in keep]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument("--deep", action="store_true",
                    help="also remove repo mirrors and the HF dataset cache")
    args = ap.parse_args()

    trees = REPO_ROOT / "data" / "repos" / "trees"
    bare = REPO_ROOT / "data" / "repos" / "bare"
    hf_cache = Path.home() / ".cache" / "huggingface"
    noise = harness_noise_files()
    noise_gb = sum(p.stat().st_size for p in noise) / 1e9

    plan: list[tuple[str, float, bool]] = [
        ("docker images (re-pull on demand)", docker_images_gb(), True),
        ("data/repos/trees (regenerable via git archive)", du_gb(trees), True),
        (f"harness logs — {len(noise)} noise files (report.json/patch.diff kept)", noise_gb, True),
        ("data/repos/bare (re-clonable mirrors)", du_gb(bare), args.deep),
        ("~/.cache/huggingface (re-downloadable)", du_gb(hf_cache), args.deep),
    ]

    mode = "EXECUTING" if args.execute else "DRY RUN — nothing deleted (use --execute)"
    print(f"{mode}\n")
    total = 0.0
    for label, gb, selected in plan:
        mark = "->" if selected else "  (skipped; needs --deep)"
        print(f"  {mark} {label:<58} {gb:7.2f} GB")
        if selected:
            total += gb
    print(f"\n  reclaimable now: ~{total:.1f} GB")

    if not args.execute:
        return 0

    subprocess.run(["docker", "system", "prune", "-a", "-f", "--volumes"], capture_output=True)
    if trees.exists():
        shutil.rmtree(trees)
    for p in noise:
        p.unlink(missing_ok=True)
    if args.deep:
        if bare.exists():
            shutil.rmtree(bare)
        if hf_cache.exists():
            shutil.rmtree(hf_cache)

    print("\ndone. Remaining footprint:")
    for label, path in (("data/", REPO_ROOT / "data"), ("logs/", REPO_ROOT / "logs"),
                        ("runs/", REPO_ROOT / "runs")):
        print(f"  {label:<8} {du_gb(path):6.2f} GB")
    print("\nLast step (GUI only): Docker Desktop -> Settings -> Resources -> lower the")
    print("virtual disk limit and Apply, so the VM's disk file on the host shrinks too.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
