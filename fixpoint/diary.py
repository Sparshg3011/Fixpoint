"""The run diary: the append-only event stream the UI renders.

This is the contract between the agent and every viewer of it. The agent never
talks to a UI; it appends one JSON object per line to runs/<run_id>.jsonl as it
works. A live view tails the file; the demo replays a recorded one at its
original pacing. Same renderer either way — which is what makes "demo mode"
honest: it is a real run played back, not a script.

Design rules that keep it useful:
  * one event per line (JSONL), so a partial file is still parseable and a
    tail -f works without any framing protocol;
  * events carry a wall-clock ts, so replay can reproduce real pacing;
  * detail payloads stay SMALL — log tails, file lists, diff snippets — never
    whole files, or the diary becomes unreadable and unshippable;
  * stages and event names are a closed vocabulary the UI switches on.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = REPO_ROOT / "runs"

# Closed vocabularies. The UI maps stages to avatars and events to visual state,
# so adding a value here means touching the renderer too.
STAGES = ("sandbox", "retrieval", "reproducer", "developer", "tester", "loop")
EVENTS = ("started", "progress", "succeeded", "failed")

# Payload fields get truncated to this many characters. Diffs and log tails are
# the point; whole files are not.
MAX_DETAIL_CHARS = 4000


@dataclass
class Event:
    ts: float
    run_id: str
    instance_id: str
    stage: str
    event: str
    attempt: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


def _truncate(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_DETAIL_CHARS:
        return value[:MAX_DETAIL_CHARS] + f"\n… truncated ({len(value)} chars)"
    if isinstance(value, list):
        return [_truncate(v) for v in value[:50]]
    return value


class Diary:
    """Append-only writer for one run.

    Usable as a context manager; `path` is stable and safe to tail while the
    run is still in progress.
    """

    def __init__(self, run_id: str, instance_id: str, runs_dir: Path | None = None):
        self.run_id = run_id
        self.instance_id = instance_id
        self.path = (runs_dir or RUNS_DIR) / f"{run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, stage: str, event: str, *, attempt: int = 0, **detail: Any) -> Event:
        """Append one event. Unknown stage/event names fail loudly — a typo that
        silently produces an unrenderable diary is worse than a crash."""
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
        if event not in EVENTS:
            raise ValueError(f"unknown event {event!r}; expected one of {EVENTS}")
        ev = Event(ts=time.time(), run_id=self.run_id, instance_id=self.instance_id,
                   stage=stage, event=event, attempt=attempt,
                   detail={k: _truncate(v) for k, v in detail.items()})
        with self.path.open("a") as f:
            f.write(json.dumps(asdict(ev)) + "\n")
        return ev

    def __enter__(self) -> Diary:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def read(path: Path) -> list[Event]:
    """Load a diary back. Tolerates a truncated final line — a run that is
    still writing, or was killed, should still be viewable."""
    events: list[Event] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(Event(**json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue  # partial final line while the run is in flight
    return events
