"""The Fixpoint web backend: benchmark results + run replay/live streaming.

Design rule, absolute: NOTHING is hardcoded. Every model, number, and row is
discovered from artifacts on disk at request time:

    data/singleshot/<model>/results.json    generation rows (diffs, errors, cost)
    data/singleshot/<model>/graded.json     official-harness verdicts
    data/calibration/calibration-*.json     harness red/green calibration
    runs/*.jsonl                            run diaries (the event streams)

Drop in a new model's artifacts and it appears; delete a directory and it
vanishes. The UI is a pure renderer of these endpoints.

    python -m fixpoint.server   # serves API + the web/ frontend on :8765
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from fixpoint.diary import EVENTS, STAGES, read

REPO_ROOT = Path(__file__).resolve().parent.parent
SINGLESHOT = REPO_ROOT / "data" / "singleshot"
LOOP = REPO_ROOT / "data" / "loop"
SHELL = REPO_ROOT / "data" / "shell"
CALIBRATION = REPO_ROOT / "data" / "calibration"
RUNS = REPO_ROOT / "runs"
WEB = REPO_ROOT / "web"

app = FastAPI(title="fixpoint", docs_url=None, redoc_url=None)


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _model_dirs() -> list[tuple[str, Path]]:
    """(mode, dir) for every model artifact directory on disk. Two modes exist:
    single-shot (one attempt, no execution feedback) and loop (reproducer-driven
    replan) — the scoreboard must say which is which, they are different claims."""
    out: list[tuple[str, Path]] = []
    for mode, root in (("single-shot", SINGLESHOT), ("loop", LOOP), ("shell", SHELL)):
        if root.exists():
            # Only direct children; archives (e.g. n100-archive) nest deeper on purpose.
            out.extend((mode, d) for d in sorted(root.iterdir()) if d.is_dir())
    return out


_DIR_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _resolve_dir(model_dir: str) -> Path | None:
    """Instance endpoints address a row as 'name' (single-shot, back-compat)
    or 'loop:name'. The name is validated as a single plain path component
    BEFORE touching the filesystem — a lexical parent check alone is
    bypassable ('loop:..' has parent == root without ever resolving)."""
    mode, _, name = model_dir.rpartition(":")
    if mode not in ("", "loop", "shell") or not _DIR_NAME_RE.fullmatch(name) or ".." in name:
        return None
    root = {"loop": LOOP, "shell": SHELL}.get(mode, SINGLESHOT)
    d = root / name
    return d if d.is_dir() else None


@app.get("/api/meta")
def meta() -> dict:
    """The diary vocabulary, so the frontend renders exactly what exists."""
    return {"stages": list(STAGES), "events": list(EVENTS)}


@app.get("/api/results")
def results() -> dict:
    """Scoreboard: one row per model directory found on disk."""
    models = []
    for mode, d in _model_dirs():
        res = _load(d / "results.json")
        graded = _load(d / "graded.json")
        if not res and not graded:
            continue
        n = (graded or {}).get("total_predictions") or (res or {}).get("n") or 0
        rows = (res or {}).get("rows", [])
        applied = sum(1 for r in rows if r.get("applied"))
        row = {
            "model": (res or {}).get("model") or (graded or {}).get("model") or d.name,
            "mode": mode,
            "dir": d.name if mode == "single-shot" else f"{mode}:{d.name}",
            "n": n,
            "generated": len(rows),
            "applied": applied,
            "apply_rate": applied / len(rows) if rows else None,
            "localized": sum(1 for r in rows
                             if (r.get("gold_retrieved_rank") or 99) <= 5),
            "cost_usd": (res or {}).get("total_cost_usd", 0.0),
            "wall_s": (res or {}).get("wall_s"),
            "graded": (graded or {}).get("graded", 0),
            "resolved": (graded or {}).get("resolved", 0),
            "empty": (graded or {}).get("empty", 0),
            # None (renders as "—") until at least one instance was actually
            # graded — an ungraded model showing "0.0% resolved" would be a lie.
            "resolve_rate": ((graded or {}).get("resolved", 0) / n)
                            if n and (graded or {}).get("graded", 0) else None,
        }
        models.append(row)
    # Headline = highest resolve rate among models graded on the largest n.
    models.sort(key=lambda m: (-(m["n"] or 0), -(m["resolve_rate"] or 0)))
    return {"models": models}


@app.get("/api/results/{model_dir}/instances")
def instances(model_dir: str) -> dict:
    d = _resolve_dir(model_dir)
    if d is None:
        raise HTTPException(404, "unknown model")
    res = _load(d / "results.json") or {}
    graded = (_load(d / "graded.json") or {}).get("per_instance", {})
    out = []
    for r in res.get("rows", []):
        iid = r["instance_id"]
        verdict = ("resolved" if graded.get(iid) else
                   "unresolved" if iid in graded else
                   "empty" if not (r.get("diff") or "").strip() else "ungraded")
        out.append({
            "instance_id": iid,
            "verdict": verdict,
            "applied": bool(r.get("applied")),
            "gold_rank": r.get("gold_retrieved_rank"),
            "guided": bool(r.get("guided_retrieval")),
            "loop_green": r.get("loop_green"),  # loop rows only; None elsewhere
            "error": r.get("error"),
            "cost_usd": r.get("cost_usd", 0.0),
        })
    out.sort(key=lambda x: x["instance_id"])
    return {"model": res.get("model", model_dir), "instances": out}


@app.get("/api/results/{model_dir}/instances/{instance_id}")
def instance_detail(model_dir: str, instance_id: str) -> dict:
    d = _resolve_dir(model_dir)
    if d is None:
        raise HTTPException(404, "unknown model")
    res = _load(d / "results.json") or {}
    for r in res.get("rows", []):
        if r["instance_id"] == instance_id:
            graded = (_load(d / "graded.json") or {}).get("per_instance", {})
            return {
                "instance_id": instance_id,
                "diff": r.get("diff", ""),
                "error": r.get("error"),
                "top_files": r.get("top_files", []),
                "gold_files": r.get("gold_files", []),
                "gold_rank": r.get("gold_retrieved_rank"),
                "resolved": bool(graded.get(instance_id)),
                "graded": instance_id in graded,
                "tokens": {"in": r.get("input_tokens"), "out": r.get("output_tokens")},
                "cost_usd": r.get("cost_usd", 0.0),
                # Loop rows carry their trajectory; single-shot rows have neither.
                "loop_green": r.get("loop_green"),
                "trajectory": r.get("trajectory"),
            }
    raise HTTPException(404, "unknown instance")


@app.get("/api/calibration")
def calibration() -> dict:
    out = []
    for f in sorted(CALIBRATION.glob("calibration-*.json")):
        if (data := _load(f)) is not None:
            out.append(data)
    return {"calibrations": out}


@app.get("/api/runs")
def runs() -> dict:
    items = []
    for f in sorted(RUNS.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        events = read(f)
        if not events:
            continue
        last = events[-1]
        items.append({
            "run_id": events[0].run_id,
            "instance_id": events[0].instance_id,
            "events": len(events),
            "started": events[0].ts,
            "duration_s": round(last.ts - events[0].ts, 1),
            "green": bool(last.detail.get("green")) if last.stage == "loop" else None,
            "live": last.stage != "loop",  # no terminal loop event yet -> in flight
        })
    return {"runs": items}


@app.get("/api/runs/{run_id}")
def run_events(run_id: str) -> dict:
    path = RUNS / f"{run_id}.jsonl"
    if not path.exists() or path.parent != RUNS:
        raise HTTPException(404, "unknown run")
    return {"events": [vars(e) for e in read(path)]}


@app.get("/api/runs/{run_id}/stream")
async def run_stream(run_id: str) -> StreamingResponse:
    """SSE tail of a diary. Replays what exists, then follows appends until the
    terminal loop event lands. Same event shape as /api/runs/{id} — the
    frontend renders live and replay through identical code."""
    path = RUNS / f"{run_id}.jsonl"
    if not path.exists() or path.parent != RUNS:
        raise HTTPException(404, "unknown run")

    async def gen():
        offset = 0
        idle = 0.0
        while True:
            for e in read(path)[offset:]:
                offset += 1
                idle = 0.0
                yield f"data: {json.dumps(vars(e))}\n\n"
                if e.stage == "loop":  # terminal
                    return
            await asyncio.sleep(0.5)
            idle += 0.5
            if idle > 600:  # abandoned run; stop holding the connection
                return

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------------------
# The product flow: submit a repo + issue, watch it live, open a PR on green.
# ---------------------------------------------------------------------------

@app.post("/api/fix")
async def submit_fix(body: dict) -> dict:
    from fixpoint import service

    try:
        run_id = await asyncio.to_thread(
            service.start_fix,
            body.get("repo", ""), body.get("issue_text"), body.get("issue_url"),
            body.get("ref") or None, body.get("model") or None)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return {"run_id": run_id}


@app.get("/api/fix/{run_id}")
def fix_meta(run_id: str) -> dict:
    from fixpoint import service

    meta = service.get_meta(run_id)
    if meta is None:
        raise HTTPException(404, "unknown fix run")
    # The diff can be large; the drawer wants it, the status poll does not.
    return {**meta, "has_diff": bool(meta.get("diff", "").strip())}


@app.post("/api/fix/{run_id}/pr")
async def fix_pr(run_id: str, body: dict) -> dict:
    """Open (or dry-run) a PR carrying this run's patch — always on the
    authenticated user's own fork; fixpoint.pr refuses anything else."""
    from fixpoint import pr as pr_mod
    from fixpoint import service

    meta = service.get_meta(run_id)
    if meta is None:
        raise HTTPException(404, "unknown fix run")
    if not meta.get("diff", "").strip():
        raise HTTPException(400, "this run produced no patch")
    try:
        res = await asyncio.to_thread(
            pr_mod.open_pr,
            upstream=meta["repo"], base_commit=meta["commit"], patch=meta["diff"],
            instance_id=meta["run_id"], problem_statement=meta.get("issue_text", ""),
            resolved=False, dry_run=not body.get("execute", False))
    except pr_mod.PRSafetyError as e:
        raise HTTPException(403, str(e)) from e
    except Exception as e:
        raise HTTPException(500, str(e)) from e
    return {"url": res.url, "branch": res.branch, "base_branch": res.base_branch,
            "dry_run": res.dry_run}


# Frontend last, so /api/* wins routing. html=True serves index.html at /.
if WEB.exists():
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
