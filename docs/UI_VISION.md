# UI vision — locked early so step 6 honors it

North star: **watchable and real.** Every pixel is driven by recorded run
events. "Demo mode" is a replay of a genuine run at its original pacing —
nothing staged, nothing hardcoded, no fabricated data anywhere in the UI.

## The cast (avatars)

The pipeline stages become on-screen characters. This is a *visual metaphor*
over a staged pipeline — the backend has no personas (see PLAN.md for why).

| Avatar | Backend stage | States |
|---|---|---|
| Scout | retrieval | idle → searching → done |
| Reproducer | reproducer script | idle → writing → red-confirmed |
| Developer | patcher + sanitizer | idle → drafting → applied / rejected |
| Tester | sandbox test run | idle → running → green / red |
| Conductor | the bounded loop | narrates attempts, decides retry vs. stop |

The hero moment: Tester flips red → green. Everything in the design serves
that beat — the diff pane settles, the failure line resolves, the run card
turns green, "Open PR" lights up.

## The contract that makes avatars possible: the run diary

The agent appends one JSON event per line to `runs/<run_id>.jsonl` as it
works. The UI is a dumb renderer of this stream — live mode tails it (SSE),
demo mode replays a recorded file with original timestamps. Same renderer,
so a demo cannot drift from reality.

```json
{"ts": 1752170000.12, "run_id": "r-2107-a", "instance_id": "django__django-11099",
 "attempt": 1, "stage": "tester", "event": "failed",
 "detail": {"summary": "reproducer red: unicode case", "log_tail": "..."}}
```

Rules: `stage` ∈ {sandbox, retrieval, reproducer, developer, tester, loop};
`event` ∈ {started, progress, succeeded, failed}; `detail` is stage-specific
and small (log tails, file lists, diff snippets — enough to render, never
whole files). Avatar state machines consume exactly these fields.

## Vibe

Mission-control dark theme, monospace accents for ids/diffs/logs, restrained
motion (avatars breathe, they don't bounce), color reserved for meaning:
red = failing signal, green = passing, amber = patch/apply trouble. Custom
design and components — no UI template — since the frontend is itself a
portfolio exhibit. Two pages only:

1. **Run** — task picker · avatar rail · live step feed · diff pane · test
   output pane · Open PR (enabled on green).
2. **Results** — headline metric, per-instance table with status chips,
   row click replays that run.

Stack: Next.js (Vercel) + typed event consumers; FastAPI backend for live
runs; GitHub API (bot token) for PRs against our fork.
