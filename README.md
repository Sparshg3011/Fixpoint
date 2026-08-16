# Fixpoint

[![ci](https://github.com/Sparshg3011/Fixpoint/actions/workflows/ci.yml/badge.svg)](https://github.com/Sparshg3011/Fixpoint/actions/workflows/ci.yml)

An agent that takes a GitHub issue and a repo, finds the code that matters,
writes a patch, runs the repo's own tests in a sandbox, and replans from the
failures until they go green — then opens a PR. Benchmarked blind on
[SWE-bench-Lite](https://www.swebench.com) (300 real GitHub issues).

## Headline result

> **23.0% of SWE-bench-Lite resolved (69/300), entirely on free open-weight
> models.** Single-shot with GLM-5.2 via NVIDIA NIM, all 300 instances, graded
> by the unmodified official Docker harness. **Total API cost: $0.00.**
> 95% CI [18.6%, 28.1%]. One instance could not be graded (its image is
> unavailable for this platform) and is counted as unresolved — the figure is
> a lower bound. The deterministic n=25 subset predicted 24%; the full run
> delivered 23.0%, validating the subset methodology.
>
> Reproduce (no paid API key needed — get a free one at build.nvidia.com):
> `python scripts/run_singleshot.py --n 300` then
> `python scripts/grade_chunked.py --predictions data/singleshot/z-ai_glm-5.2/predictions.jsonl`.
> Dataset fingerprint `b4200a5b…015a`.
>
> **The scaffold is not model-specific.** Measured on the identical 25
> instances, same prompt, same retrieval, same harness:
>
> | model | resolved | apply rate | apply *given retrieval hit* | cost |
> |---|---|---|---|---|
> | z-ai/glm-5.2 (free) | **6/25 = 24%** | 72% | **94%** | **$0.00** |
> | claude-sonnet-5 (paid) | 9/25 = 36% | 64% | **94%** | $7.21 |
>
> The conditional apply rate — the honest measure of the patcher once
> retrieval does its job — is **identical at 94%**. Retrieval's recall@5 = 64%
> is the shared ceiling, not the model. See
> [docs/PATCHING.md](docs/PATCHING.md) for the full comparison, including a
> third model that scored 4% and why half of that gap turned out to be our
> own parser.
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
