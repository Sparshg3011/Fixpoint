# The complete results ladder

Every configuration ever benchmarked, on identical instances with identical
official-harness grading — so each row isolates what its architecture or
scaffold revision adds. The README carries only the headline; this is the
full record.

| architecture | model (free) | scope | resolved | cost |
|:---|:---|:---|:---|:---|
| **Interactive shell agent** | Nemotron-3-Ultra | Lite n=100 stratified | **47%** — CI [38%, 57%] | **$0.00** |
| Single-shot pipeline (v2) | Nemotron-3-Ultra | Lite n=100 stratified | 44% — CI [35%, 54%] | $0.00 |
| Single-shot pipeline (v2) | Nemotron-3-Ultra | **full Lite-300** | **32.7%** — CI [28%, 38%] | $0.00 |
| Replan loop (keep-first) | Nemotron-3-Ultra | Lite n=100 stratified | 43% — CI [33%, 53%] | $0.00 |
| Replan loop (keep-latest, superseded) | Nemotron-3-Ultra | Lite n=100 stratified | 41% — CI [32%, 51%] | $0.00 |
| Single-shot pipeline (v1) | Nemotron-3-Ultra | Lite n=100 stratified | 37% — CI [28%, 47%] | $0.00 |
| Single-shot pipeline (v1) | GLM-5.2 † | full Lite-300 | 23.0% — CI [19%, 28%] | $0.00 |

† Retired by NVIDIA (end-of-life 2026-08-21) — the run remains archived and
reproducible against the pinned dataset fingerprint.

**Scaffold revisions.** v2 = v1 plus four measured upgrades: mention-first
retrieval (localization@5 68%→70%, zero regressions), a fuzzy edit-matcher
tier (threshold swept on saved responses), whole-corpus edit targets, and a
truncation retry that cut reasoning-burn losses from 64 instances to 18. The
shell agent replaces the pipeline entirely: the model works inside the sealed
instance container and the patch is `git diff`.

**Reading the ladder.** The interesting comparisons are vertical: the same
100 instances scored 37 → 44 → 47 as the scaffold, then the architecture,
changed — while the replan loop's keep-latest policy *lost* to single-shot
until the traced keep-first fix closed the gap ([docs/REPLAN.md](REPLAN.md)).
Negative results are part of the record, not an embarrassment
([docs/SHELL.md](SHELL.md)).

## SWE-bench Verified

| architecture | model (free) | scope | resolved | cost |
|:---|:---|:---|:---|:---|
| **Interactive shell agent** | Nemotron-3-Ultra | Verified n=50 stratified | **64%** — CI [50%, 76%] | **$0.00** |

## Engine comparison (identical scaffold, identical Lite n=25 instances)

| model (free) | resolved | applied | notes |
|:---|:---|:---|:---|
| **MiniMax M3** | **17/25 = 68%** | 22/25 | 77% of graded patches resolved |
| Nemotron-3-Ultra | 11/25 = 44% | 18/25 | the scaffold-era baseline |

The 24-point gap is pure engine — same shell agent, same sealed containers,
same grading. A Verified n=50 campaign with M3 is in flight.
