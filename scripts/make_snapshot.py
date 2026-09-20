#!/usr/bin/env python
"""Build the deployment snapshot: exactly what the UI shows locally, nothing else.

The scoreboard and the run replays are rendered from artifacts that are
gitignored on purpose (`data/`, `runs/` — gigabytes of mirrors, logs and raw
model output live beside them). A deployment still needs the few megabytes the
UI actually reads, so this script copies precisely that set into
`deploy/snapshot/`, which IS committed and is what the Docker image bakes in.

Discovery mirrors the server's own rule — direct children of data/singleshot,
data/loop and data/shell that hold a results.json or graded.json, plus the
top-level diaries in runs/ — so curating what the local UI shows (archiving a
model directory, archiving red runs) curates the deployment too.

One transformation only: `raw_response` is dropped from result rows. It is the
bulk of those files, no endpoint serves it, and the archived originals keep it.

One exclusion, and it fails CLOSED: product-flow runs (`fix-*`) are recordings
of arbitrary repositories — a diary holds file names, a sidecar holds a diff —
and on the operator's own machine git will happily read PRIVATE repos through
the keychain. Such a run ships only if GitHub's unauthenticated API itself says
the repository is public; if that cannot be confirmed (private, deleted,
offline, rate-limited) the run stays home. Benchmark replays are all public OSS.

    python scripts/make_snapshot.py          # rebuild deploy/snapshot/
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "deploy" / "snapshot"
ARTIFACTS = OUT / "artifacts"   # becomes data/ inside the image
REPLAYS = OUT / "replays"       # seeds runs/ on first boot

# Hugging Face Spaces (one supported host) rejects plain-git files over 10 MB.
MAX_FILE_BYTES = 9_500_000


def _copy_model_dir(src: Path, dst: Path) -> int:
    dst.mkdir(parents=True, exist_ok=True)
    rows = 0
    results = src / "results.json"
    if results.exists():
        data = json.loads(results.read_text())
        for row in data.get("rows", []):
            row.pop("raw_response", None)
        rows = len(data.get("rows", []))
        (dst / "results.json").write_text(json.dumps(data, separators=(",", ":")))
    if (src / "graded.json").exists():
        shutil.copy2(src / "graded.json", dst / "graded.json")
    return rows


def _is_public_repo(repo: str) -> bool:
    """True only on positive confirmation from GitHub, asked WITHOUT credentials."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(f"https://api.github.com/repos/{repo}",
                                 headers={"Accept": "application/vnd.github+json",
                                          "User-Agent": "fixpoint-snapshot"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200 and json.load(r).get("private") is False
    except (urllib.error.URLError, ValueError, TimeoutError):
        return False


def _fix_run_is_shippable(diary: Path) -> tuple[bool, str]:
    sidecar = diary.with_suffix(".meta.json")
    try:
        repo = json.loads(sidecar.read_text())["repo"]
    except (OSError, ValueError, KeyError):
        return False, "no readable sidecar naming its repository"
    if _is_public_repo(repo):
        return True, repo
    return False, f"{repo} is not confirmably public"


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)  # a removed local artifact must vanish from the snapshot too

    models = rows = 0
    for mode in ("singleshot", "loop", "shell"):
        root = ROOT / "data" / mode
        if not root.exists():
            continue
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            if not ((d / "results.json").exists() or (d / "graded.json").exists()):
                continue
            rows += _copy_model_dir(d, ARTIFACTS / mode / d.name)
            models += 1

    calib = 0
    for f in sorted((ROOT / "data" / "calibration").glob("calibration-*.json")):
        (ARTIFACTS / "calibration").mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, ARTIFACTS / "calibration" / f.name)
        calib += 1

    REPLAYS.mkdir(parents=True, exist_ok=True)
    runs = 0
    withheld: list[str] = []
    for f in sorted((ROOT / "runs").glob("*.jsonl")):
        if f.name.startswith("fix-"):
            ok, why = _fix_run_is_shippable(f)
            if not ok:
                withheld.append(f"{f.name}: {why}")
                continue
        shutil.copy2(f, REPLAYS / f.name)
        sidecar = f.with_suffix(".meta.json")
        if sidecar.exists():
            shutil.copy2(sidecar, REPLAYS / sidecar.name)
        runs += 1

    files = [f for f in OUT.rglob("*") if f.is_file()]
    total = sum(f.stat().st_size for f in files)
    oversized = [f for f in files if f.stat().st_size > MAX_FILE_BYTES]
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    (OUT / "MANIFEST.json").write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_commit": commit, "model_dirs": models, "result_rows": rows,
        "calibrations": calib, "replays": runs, "bytes": total,
    }, indent=2) + "\n")

    print(f"snapshot -> {OUT.relative_to(ROOT)}")
    print(f"  {models} model dirs ({rows} result rows), {calib} calibrations, {runs} replays")
    print(f"  {len(files)} files, {total / 1e6:.1f} MB")
    for line in withheld:
        print(f"  withheld  {line}")
    for f in oversized:
        print(f"  ! {f.relative_to(ROOT)} is {f.stat().st_size / 1e6:.1f} MB — over the 10 MB "
              "plain-git limit some hosts enforce", file=sys.stderr)
    return 1 if oversized else 0


if __name__ == "__main__":
    raise SystemExit(main())
