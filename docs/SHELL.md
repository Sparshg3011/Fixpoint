# The interactive shell agent

The architecture the current leaderboard runs on: the model works inside the
official instance container with a bash shell — explores, edits, runs tests,
submits. No retrieval stage, no edit format, no sanitizer: the patch is
`git diff`. Firewall enforced in `harness/interactive.py`: no network (the
real fixes are on public GitHub), git history scrubbed and PROVEN clean of
post-base commits before the agent's first command (fail-closed assertion,
calibrated to the images' one environment-setup commit).

## Campaign journal (nemotron-ultra, deterministic n=25 subset)

| run | change under test | resolved | applied | empty | submitted |
|---|---|---|---|---|---|
| v1 | baseline protocol | **11/25 = 44%** | 18 | 7 | 2 |
| v2 | + wander guard (halfway-with-no-edits warning) | 8/25 = 32% | 14 | 11 | 3 |

v1 matches the best single-shot subset number on its first untuned campaign,
with a 61% applied→resolved conversion (single-shot: 40%) — the agent's
patches are better because it tests them before we ever see them.

**v2 is a traced negative result, reverted.** The guard was aimed at 4
transcripts that explored their whole budget without editing. It fired on 17
of 25 instances instead (halfway through the budget, most runs legitimately
haven't edited yet — exploration IS the work), and the per-instance trace
shows the damage: all three instances that flipped resolved→unresolved had
the guard fire; two of them had produced clean resolving fixes in v1 and
ended v2 with EMPTY diffs after being told to "commit NOW" mid-investigation.
Zero instances improved. Same law the replan loop taught: pressure without
signal degrades an agent. (The submit-reminder line rode in both runs and is
not implicated.)

Both runs' full artifacts are archived (`baseline-v1-archive/`,
`wander-guard-v2-archive/`) with per-instance transcripts.

## n=100 campaign (nemotron-ultra, 2026-08-21)

| metric | value |
|---|---|
| RESOLVED | **47/100 = 47.0%** (CI [37.6%, 56.6%]) |
| applied | 71 (66% of applying patches resolve) |
| submitted voluntarily | 24 |
| same instances, single-shot v2 | 44/100 |
| same instances, single-shot v1 | 37/100 |

Two infrastructure lessons were paid for and banked along the way. A 429
storm plus a window where the congested endpoint returned EMPTY replies cost
29 instances on the first pass (the loop miscounted blank replies as the
agent breaking protocol — 19 of 20 "drift" deaths were actually the API
giving nothing). Fixes: empty replies retry at the turn level, and quota
deaths are never recorded as rows at all — a campaign that hits a quota wall
now pauses itself rather than logging failures that say nothing about the
instances. The recovery run regenerated exactly those 29 and the score moved
35 -> 47 without touching the agent.
