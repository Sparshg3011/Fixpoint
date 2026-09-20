"""`git apply --check` in a throwaway repo — the one verdict every mode shares.

Lives at the package root on purpose: the benchmark runners and the hosted
product flow both need it, and the product flow must not import the benchmark
stack to get it (that import edge used to pull `datasets`, pandas and pyarrow
into the web server — a gigabyte of image for one 20-line function).
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

# The `diff --git a/<path> b/<path>` header line names each file the diff edits.
_DIFF_GIT_RE = re.compile(r"^diff --git a/(\S+) b/\S+$", re.MULTILINE)


def touched_paths(diff: str) -> list[str]:
    return _DIFF_GIT_RE.findall(diff)


def git_apply_check(tree: Path, diff: str) -> bool:
    """Real `git apply --check` — the harness's own first apply command — run in
    a throwaway repo seeded with just the files the diff touches at their real
    paths. Returns True iff git would apply it cleanly.
    """
    paths = touched_paths(diff)
    if not paths:
        return False
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for rel in paths:
            src = tree / rel
            if not src.exists():
                return False
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(src.read_text(errors="replace"))
        (root / "p.diff").write_text(diff)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        return subprocess.run(["git", "apply", "--check", "p.diff"],
                              cwd=root, capture_output=True).returncode == 0
