# Fixpoint — locked plan

An agent that takes a repo + GitHub issue, retrieves the relevant files
(BM25 + embeddings), writes a unified diff, verifies it against its own
reproducer + regression tests in a Docker sandbox, replans from failure output
(max 3 attempts), and emits a final diff — graded blind by the official
SWE-bench harness on Lite-300.

Headline metric: **% of SWE-bench-Lite resolved.** Honest solo two-week
target: teens. Every published number cites the dataset fingerprint
(`fixpoint.bench.loader.EXPECTED_FINGERPRINT`), the model version, and a
config hash.

## Locked decisions

- Benchmark: SWE-bench_Lite test split (300), fingerprint-pinned. ~25-instance
  subset for iteration; full 300 only for headline runs.
- Architecture: staged pipeline — reproducer → developer → tester under a
  bounded loop. Stages are functions with focused LLM calls, not chatty
  persona agents (evidence: Agentless-style pipelines beat free-roam scaffolds
  on resolve rate per dollar). Best-of-N candidate patches is a step-5 knob,
  adopted only if it buys measured uplift.
- Firewall: agent-side code imports `AgentView` only. `gold_patch`,
  `test_patch`, `FAIL_TO_PASS`, `PASS_TO_PASS`, `hints_text` never cross.
  Enforced by types + `scripts/leak_audit.sh`.
- Inner-loop signal: self-written reproducer + existing nearby tests. The
  graded tests are never run at solve time. The inner-green → RESOLVED gap
  (reproducer overfitting) is measured, not assumed away.
- Demo: deterministic replay of recorded run diaries (`runs/*.jsonl`). Live
  mode confined to the 12 benchmark repos, rate-limited. PRs via bot token to
  our fork — never upstream.
- Cut: GitHub OAuth, anonymous arbitrary-repo execution, "fix all issues"
  batch mode, persona multi-agent, any third UI page.

## Phases and gates

| Phase | Build | Gate to advance | Number produced |
|---|---|---|---|
| 0 ✅ | dataset + visibility boundary | homework review | fingerprint; dataset stats |
| 1 | sandbox + official harness | empty→red AND gold→green on django__django-11099 | calibration n=1, then n=25 |
| 2 | hybrid retrieval | recall@5 beats "issue names the file" baseline | recall@{1,5,10} |
| 3 | patcher + diff sanitizer | ≥90% of diffs apply cleanly | apply rate; single-shot resolve % |
| 4 | reproducer + replan loop | measured uplift over single-shot | resolve %/attempt; cost/instance |
| 5 | full eval + taxonomy + contamination audit | headline reproduces twice | **% of Lite-300 resolved** |
| 6 | UI, PR flow, deploy, README | demo runs with zero live dependencies | deployed URL + demo |

Ownership: infrastructure, plumbing, and UI are pair-built with the agent;
the three cruxes (retrieval scoring, sanitizer, replan loop) are written by
hand against defined interfaces, then reviewed line by line. That split is
the point of the project.

## Risks, in priority order

1. Apple Silicon vs. prebuilt x86 harness images — tested on day 1;
   fallback is local arm64 builds (`--namespace none`) or a small x86 box
   for grading only.
2. Disk: harness images are GBs each — pull per-subset, `--cache_level`
   tuned, prune between phases.
3. Patch-apply failures — owned by the sanitizer and its ≥90% gate.
4. Reproducer overfitting — owned by the conversion metric.
5. Cost — per-instance budget caps; subset iteration; full runs only at
   step 5 (~$50–150/run, tracked per instance).

## Scoreboard (what the README publishes)

Resolve % (headline) · resolve-per-attempt curve · localization recall@5 vs.
free-localization baseline · patch apply rate · inner-green→RESOLVED
conversion · cost + latency per instance · error taxonomy (wrong file / bad
diff / wrong fix / P2P regression / budget spent / env error) ·
contamination audit result.
