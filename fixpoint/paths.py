"""Where Fixpoint keeps mutable state.

On a laptop everything lives in the repo checkout and nobody thinks about it.
On a host the image is read-only-ish and disposable, while two things must
survive restarts and redeploys: recorded run diaries, and the repo mirror
cache (re-cloning django on every cold start is minutes of dead air). Both
hang off one directory so a single mounted volume covers them:

    FIXPOINT_STATE_DIR=/state   ->   /state/runs, /state/data/repos

Unset, the state dir is the repo root — local behavior is byte-for-byte what
it always was. Benchmark artifacts (data/singleshot, ...) are NOT state: they
ship inside the image and are only ever read.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ.get("FIXPOINT_STATE_DIR") or REPO_ROOT)

RUNS_DIR = STATE_DIR / "runs"
REPOS_DIR = STATE_DIR / "data" / "repos"
