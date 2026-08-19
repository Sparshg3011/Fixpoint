# Fixpoint

[![ci](https://github.com/Sparshg3011/Fixpoint/actions/workflows/ci.yml/badge.svg)](https://github.com/Sparshg3011/Fixpoint/actions/workflows/ci.yml)

An agent that takes a GitHub issue and a repo, finds the code that matters,
writes a patch, runs the repo's own tests in a sandbox, and replans from the
failures until they go green — then opens a PR. Benchmarked blind on
[SWE-bench-Lite](https://www.swebench.com) (300 real GitHub issues).

## Headline result

> **32.7% of SWE-bench-Lite resolved (98/300), entirely on free open-weight
> models.** Single-shot with Nemotron-3-Ultra via NVIDIA NIM, all 300
> instances, graded by the unmodified official Docker harness. **Total API
> cost: $0.00.** 95% CI [27.6%, 38.2%]. Three instances could not be graded
> (their images are unavailable for this platform) and are counted as
> unresolved — the figure is a lower bound.
>
> Reproduce (no paid API key needed — get a free one at build.nvidia.com):
> `python scripts/run_singleshot.py --n 300 --model nvidia/nemotron-3-ultra-550b-a55b`
> then `python scripts/grade_chunked.py --predictions data/singleshot/nvidia_nemotron-3-ultra-550b-a55b/predictions.jsonl`.
> Dataset fingerprint `b4200a5b…015a`.
>
> **The scaffold is not model-specific — and free models beat the paid
> frontier reference on it:**
>
> | model | scope | scaffold | resolved | cost |
> |---|---|---|---|---|
> | nvidia/nemotron-3-ultra-550b (free) | n=100 stratified | v2 single-shot | **44%** — CI [35%, 54%] | **$0.00** |
> | nvidia/nemotron-3-ultra-550b (free) | **full Lite-300** | v2 single-shot | **32.7%** — CI [28%, 38%] | **$0.00** |
> | nvidia/nemotron-3-ultra-550b (free) | n=100 stratified | v2 + replan loop | 41% — CI [32%, 51%] | **$0.00** |
> | nvidia/nemotron-3-ultra-550b (free) | n=100 stratified | v1 single-shot | 37% — CI [28%, 47%] | **$0.00** |
> | z-ai/glm-5.2 (free) | full Lite-300 | v1 single-shot | 23.0% — CI [19%, 28%] | **$0.00** |
> | claude-sonnet-5 (paid reference) | n=25 subset | v1 single-shot | 36% — CI [20%, 55%] | $7.21 |
>
> Scaffold v2 = the v1 pipeline plus measured upgrades, each validated
> offline before shipping: mention-first retrieval (localization@5 68%→70%,
> zero regressions), a fuzzy edit-matcher tier (threshold swept on saved
> responses), whole-corpus edit targets, and a truncation retry that cut
> reasoning-burn losses from 64 instances to 18. Under v2 the apply rate on
> full Lite-300 is **82.7%** (248/300); 40% of graded applying patches
> resolve. A v2 GLM re-run is queued; every v1 number above remains archived
> and reproducible. See [docs/PATCHING.md](docs/PATCHING.md) for the model
> comparison, including a model that scored 4% and why half of that gap
> turned out to be our own parser.
>
> **The loop row is a negative result, reported because it's true**: on the
> same 100 instances with the same scaffold, the replan loop scored 41 where
> single-shot scored 44. The loop's own green signal was excellent when it
> fired — 24 of its 32 greens resolved (75% precision, with zero access to
> the graded tests) — but its keep-the-latest-attempt policy meant that
> whenever the reproducer couldn't judge a patch, a blind retry could
> overwrite a correct first answer. The policy is now keep-the-first (a
> regression test pins it), and the re-measurement is queued. See
> [docs/REPLAN.md](docs/REPLAN.md).
>
> The harness itself is calibrated: 24/25 instances grade red with an empty
> patch and green with the gold patch; the 25th (psf__requests-2674) is
> network-flaky, documented in [docs/CALIBRATION.md](docs/CALIBRATION.md),
> kept in the denominator, and did not resolve — so it is not inflating
> anything.

## Why this is hard

1. **Localization** — the right ~5 files out of thousands, from prose alone.
   Measured as recall@k against gold-touched files. On the n=25 subset,
   from-scratch BM25 hits **recall@5 = 64%** (recall@10 = 72%) versus a 12%
   "issue names the file" baseline — see [docs/RETRIEVAL.md](docs/RETRIEVAL.md).
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
fixpoint/bench/       dataset access + agent-visibility firewall
fixpoint/retrieval/   BM25 + mention retrieval, checkout cache, corpus walker
fixpoint/agent/       patcher, SEARCH/REPLACE diff synthesizer, reproducer, replan loop
fixpoint/harness/     official SWE-bench eval + the reproducer sandbox
fixpoint/eval/        deterministic subset, recall@k, single-shot pipeline
scripts/              step runners + offline calibrations (verify_sanitizer, verify_reproducer, ...)
docs/                 PLAN, RETRIEVAL, PATCHING, REPLAN, CALIBRATION, UI_VISION
```

Every offline engine has a credit-free calibration you can reproduce:
`scripts/verify_sanitizer.py` (diff applies == gold), `scripts/verify_reproducer.py`
(reproducer red-on-base / green-on-gold), `scripts/calibrate.py` (harness red/green).

```bash
.venv/bin/pip install -e ".[dev]" && .venv/bin/python -m pytest   # hermetic suite
```

The suite needs no network, Docker, or API key. It pins the integrity
properties directly: the `AgentView` firewall exposes only four fields, the
replan loop never reports green off a reproducer that didn't go red on base,
subset selection and BM25 tie-breaks are deterministic, and a malformed edit
fails loudly instead of corrupting a file.
