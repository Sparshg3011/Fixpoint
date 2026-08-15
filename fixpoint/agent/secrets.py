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
    """Fail early and clearly if the ACTIVE backend has no credentials.

    Which key is required depends on FIXPOINT_BACKEND: the anthropic path needs
    ANTHROPIC_API_KEY, while an OpenAI-compatible endpoint accepts any of
    several provider-named keys — and a local server (Ollama) needs none at
    all, so an empty key there is legitimate rather than an error.
    """
    load_env()
    backend = os.environ.get("FIXPOINT_BACKEND", "anthropic")

    if backend == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit(
                "No ANTHROPIC_API_KEY found (FIXPOINT_BACKEND=anthropic).\n"
                "  Add it to .env (gitignored), or switch backends by setting\n"
                "  FIXPOINT_BACKEND=openai with FIXPOINT_BASE_URL + FIXPOINT_MODEL.\n"
                "Never paste a key into a chat or a command — put it only in .env."
            )
        return

    if not os.environ.get("FIXPOINT_BASE_URL"):
        raise SystemExit("FIXPOINT_BACKEND=openai requires FIXPOINT_BASE_URL in .env")
    # localhost servers (Ollama, vLLM) authenticate nothing; remote ones must.
    url = os.environ["FIXPOINT_BASE_URL"]
    is_local = "localhost" in url or "127.0.0.1" in url
    has_key = any(os.environ.get(k) for k in
                  ("FIXPOINT_API_KEY", "NVIDIA_API_KEY", "ZAI_API_KEY", "OPENAI_API_KEY"))
    if not has_key and not is_local:
        raise SystemExit(
            f"No API key found for {url}.\n"
            "  Set one of FIXPOINT_API_KEY / NVIDIA_API_KEY / ZAI_API_KEY / OPENAI_API_KEY in .env."
        )
