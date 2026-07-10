# Fixpoint

An agent that takes a GitHub issue and a repo, finds the code that matters,
writes a patch, runs the repo's own tests in a sandbox, and replans from the
failures until they go green — then opens a PR. Benchmarked blind on
[SWE-bench-Lite](https://www.swebench.com) (300 real GitHub issues).

## Headline result

> **Pending.** Step-1 calibration (empty patch → red, gold patch → green
> through the official Docker harness) runs first; the resolve rate lands in
> step 5. This section will always lead with the number and how to reproduce
> it — dataset fingerprint `b4200a5b…015a`, model version, config hash.

## Why this is hard

1. **Localization** — the right ~5 files out of thousands, from prose alone.
   Measured as recall@k against gold-touched files.
2. **Test-driven replanning** — turning a stack trace into a *better* next
   patch under a bounded budget, not a random retry.
3. **Verification you can trust** — the agent never sees the gold patch or
   the graded tests; its inner loop runs on a self-written reproducer, and
   the gap between "my tests pass" and "the hidden tests pass" is measured.

## Integrity

The number is only worth something if the boundary holds:

- Agent-side code imports `fixpoint.bench.AgentView` (id, repo, commit,
  issue text) and nothing else — grading fields live in a separate type.
- `scripts/leak_audit.sh` greps agent-side packages for grading-field names.
- Evaluation is the unmodified official `swebench` harness, pinned.

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python scripts/step0_explore.py      # dataset + fingerprint
.venv/bin/python scripts/calibrate.py          # red/green gate (needs Docker)
```

## Repo map

```
fixpoint/bench/     dataset access + agent-visibility firewall
fixpoint/harness/   official SWE-bench evaluation, wrapped
scripts/            step runners (explore, calibrate, leak audit)
docs/               PLAN.md (locked scope), UI_VISION.md (avatars, run diary)
```
