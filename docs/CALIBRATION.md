# Harness calibration record

Machine: Apple Silicon (arm64), Docker 29.x, x86_64 harness images under
emulation. Harness: official `swebench` 4.1.0, unmodified. Subset: the
deterministic n=25 from `fixpoint.eval.lite_subset` (proportional per repo,
floor 1, evenly spaced — 9 django, 6 sympy, 1 each of the other ten).

Method: every instance is graded twice. RED applies a no-op patch (new file,
touches nothing) and must grade unresolved — proves the harness can see the
bug. GREEN applies the dataset's gold patch and must grade RESOLVED — proves
env build, patch apply, test scoping, and log parsing end to end.

## Results (2026-07-10)

- 24/25 instances: red unresolved, green resolved. Gate PASS.
- 50 evaluations, 3 workers, 2,057s wall (~41s/eval mean under emulation).
- Images: ~57GB for 25 instances across 12 repos (layers shared per repo).
- LLM cost: $0 — calibration never calls a model.

## Known-noisy instance: psf__requests-2674

Observed (twice, independently): with the NO-OP patch the harness grades
**RESOLVED** — all 12 FAIL_TO_PASS tests pass with no fix applied. The gold
patch graded UNRESOLVED once and resolved once. The F2P list is dominated by
tests that hit live endpoints (httpbin.org) — `test_HTTP_200_OK_GET` and
friends — so verdicts on this instance track network weather, not patch
correctness. This is mined-label noise: F2P sets come from executions at
dataset-construction time, and network-dependent suites do not replay.

Handling: the instance **stays in the 300-instance denominator** (the official
verdict is the verdict, and every published number faces the same coin). Any
"resolved" we score on it will be footnoted as non-discriminative in the final
report rather than counted as skill.

Evidence: `data/calibration/calibration-s25.json`,
`data/calibration/calibration-psf__requests-2674.json` (local, gitignored;
regenerate with `python scripts/calibrate.py --subset 25`).
