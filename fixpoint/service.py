"""The product flow: a GitHub repo + an issue in, a PR-ready patch out.

This is the loop the project exists for:

    repo URL + issue  ->  clone @ ref  ->  retrieve  ->  patch (bounded retries,
    model-guided file requests)  ->  verify with real `git apply --check`
    ->  diary events throughout  ->  optional PR on the user's fork

Works on ANY public GitHub repo, not just the benchmark twelve — the checkout,
retrieval, and patching layers were always repo-agnostic. What differs from
benchmark mode is the verification tier, and we label it honestly:

    static   the diff applies cleanly to the checkout (always checked)
    sandbox  reproducer red->green in a per-instance container — only possible
             where a prebuilt environment exists (the benchmark repos)

A patch that "applies" is NOT proven to fix the issue; the PR body says
exactly which tier it passed. Runs stream to the same diary the UI renders,
so a submitted fix is watchable live at /#/runs/<run_id>.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path

from fixpoint.agent.patcher import generate_patch
from fixpoint.agent.secrets import load_env
from fixpoint.diary import RUNS_DIR, Diary
from fixpoint.eval.singleshot import git_apply_check
from fixpoint.retrieval import load_corpus, tree_at
from fixpoint.retrieval.bm25 import BM25Searcher
from fixpoint.retrieval.guided import resolve_requested_paths

_REPO_RE = re.compile(r"(?:github\.com[/:])?(?P<owner>[\w.-]+)/(?P<name>[\w.-]+?)(?:\.git|/.*)?$")
_ISSUE_URL_RE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)")


def normalize_repo(text: str) -> str:
    """'https://github.com/pallets/flask', 'pallets/flask.git' -> 'pallets/flask'."""
    m = _REPO_RE.search(text.strip())
    if not m:
        raise ValueError(f"could not parse a GitHub repo from {text!r}")
    return f"{m.group('owner')}/{m.group('name')}"


def fetch_issue(url: str) -> tuple[str, str]:
    """Resolve a GitHub issue URL to (repo, 'title\\n\\nbody') via the public API."""
    import httpx

    m = _ISSUE_URL_RE.search(url)
    if not m:
        raise ValueError(f"not a GitHub issue URL: {url!r}")
    owner, name, num = m.groups()
    r = httpx.get(f"https://api.github.com/repos/{owner}/{name}/issues/{num}",
                  headers={"Accept": "application/vnd.github+json"}, timeout=30)
    r.raise_for_status()
    data = r.json()
    return f"{owner}/{name}", f"{data.get('title', '')}\n\n{data.get('body') or ''}"


def resolve_ref(repo: str, ref: str | None) -> str:
    """A commit sha for the requested ref (default branch HEAD when None)."""
    if ref and re.fullmatch(r"[0-9a-f]{7,40}", ref):
        return ref
    target = ref or "HEAD"
    out = subprocess.run(["git", "ls-remote", f"https://github.com/{repo}.git", target],
                         capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        sha, name = line.split("\t")
        if name == target or name.endswith(f"/{target}"):
            return sha
    raise ValueError(f"ref {target!r} not found on {repo}")


def fix_issue(repo: str, issue_text: str, commit: str, *,
              model: str | None = None, k: int = 5, attempts: int = 2,
              diary: Diary | None = None) -> dict:
    """Run the fix pipeline once. Returns {diff, applied, error, files, commit}."""
    d = diary or Diary(run_id=f"adhoc-{int(time.time())}", instance_id=repo)
    kwargs = {"model": model} if model else {}

    d.record("sandbox", "started", repo=repo, commit=commit[:12])
    tree = tree_at(repo, commit)
    docs = load_corpus(tree)
    by_path = {x.path: x.text for x in docs}
    d.record("sandbox", "succeeded", corpus_files=len(docs))

    d.record("retrieval", "started", query_chars=len(issue_text))
    ranked = [p for p, _ in BM25Searcher(docs).search(issue_text, k=k)]
    files = {p: by_path[p] for p in ranked}
    d.record("retrieval", "succeeded", files=ranked)

    patch, feedback = None, None
    for attempt in range(1, attempts + 1):
        patch = generate_patch(issue_text, files, feedback=feedback, **kwargs)
        # The model asking for an unseen file is a localization hypothesis —
        # honor it once per attempt (measured: 2/5 such requests were the
        # exact file BM25 missed).
        if not patch.diff and patch.missing_paths:
            extra = resolve_requested_paths(patch.missing_paths, by_path, already_given=files)
            if extra:
                d.record("retrieval", "progress", attempt=attempt,
                         model_requested=sorted(extra))
                files = {**files, **extra}
                patch = generate_patch(issue_text, files, feedback=feedback, **kwargs)
        if patch.diff:
            d.record("developer", "succeeded", attempt=attempt, diff=patch.diff)
            break
        d.record("developer", "failed", attempt=attempt, error=patch.error)
        feedback = (f"Your previous attempt produced no usable edits: {patch.error}. "
                    "Re-read the files and produce edit blocks that copy the SEARCH "
                    "text exactly.")

    applied = bool(patch.diff) and git_apply_check(tree, patch.diff)
    d.record("tester", "succeeded" if applied else "failed",
             verification="git apply --check", applied=applied,
             output=f"git apply --check: {'clean' if applied else 'FAILED or no patch'}\n"
                    f"verification tier: static (repo tests not executed)")
    d.record("loop", "succeeded" if applied else "failed",
             green=applied, attempts=attempts, verification="static")
    return {"diff": patch.diff if patch else "", "applied": applied,
            "error": None if applied else (patch.error if patch else "no patch"),
            "files": list(files), "commit": commit}


# ---------------------------------------------------------------------------
# Async runner used by the web server: one thread per submitted fix, with a
# sidecar meta file so the PR endpoint can reconstruct everything later.
# ---------------------------------------------------------------------------

def _safe_id(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", text)


def start_fix(repo_input: str, issue_text: str | None, issue_url: str | None,
              ref: str | None = None, model: str | None = None) -> str:
    """Validate, then launch the pipeline in a background thread. Returns the
    run_id whose diary the UI can immediately watch (live via SSE)."""
    load_env()
    if issue_url and not issue_text:
        repo_from_url, issue_text = fetch_issue(issue_url)
        repo = normalize_repo(repo_input) if repo_input.strip() else repo_from_url
    else:
        repo = normalize_repo(repo_input)
    if not (issue_text or "").strip():
        raise ValueError("an issue description (or issue URL) is required")
    commit = resolve_ref(repo, ref)

    run_id = _safe_id(f"fix-{repo}-{int(time.time())}")
    meta = {"run_id": run_id, "repo": repo, "commit": commit,
            "issue_text": issue_text, "issue_url": issue_url,
            "created": time.time(), "diff": "", "applied": False}
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    _meta_path(run_id).write_text(json.dumps(meta, indent=2))

    def work():
        diary = Diary(run_id=run_id, instance_id=repo)
        try:
            result = fix_issue(repo, issue_text, commit, model=model, diary=diary)
            meta.update(diff=result["diff"], applied=result["applied"],
                        error=result["error"])
        except Exception as e:  # surfaced in the diary, never lost to a thread
            diary.record("loop", "failed", green=False, error=str(e)[:500])
            meta.update(error=str(e)[:500])
        _meta_path(run_id).write_text(json.dumps(meta, indent=2))

    threading.Thread(target=work, name=run_id, daemon=True).start()
    return run_id


def _meta_path(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.meta.json"


def get_meta(run_id: str) -> dict | None:
    p = _meta_path(_safe_id(run_id))
    return json.loads(p.read_text()) if p.exists() else None
