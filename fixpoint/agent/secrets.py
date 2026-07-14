"""Load the API key from a local .env into the process environment.

Deliberately minimal — no python-dotenv dependency, no logging of values. Entry
points (scripts that actually call the LLM) call load_env() before constructing
the Anthropic client. The key lives only in the gitignored .env file and the
process environment; it is never written to a diff, a log, or a run diary.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def load_env(path: Path | None = None) -> bool:
    """Read KEY=VALUE lines from .env into os.environ (without overriding a
    value already set in the environment). Returns True if a .env was found."""
    env_path = path or (REPO_ROOT / ".env")
    if not env_path.exists():
        return False
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)  # env var wins over .env
    return True


def require_api_key() -> None:
    """Fail early and clearly if no key is available, before any API call."""
    load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "No ANTHROPIC_API_KEY found.\n"
            "  Create a .env file (gitignored): cp .env.example .env, then paste your key.\n"
            "  Or export ANTHROPIC_API_KEY in your shell.\n"
            "Never paste the key into a chat or a command — put it only in .env."
        )
