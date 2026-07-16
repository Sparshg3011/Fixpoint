# Replanning — the bounded test-driven loop

The second crux: read a failure and produce a *better* next patch, under a
bounded budget, without ever seeing the graded tests.

## The loop

    write a reproducer from the issue  (LLM)
    confirm it goes RED on base         (sandbox — else the compass is broken)
    repeat up to max_attempts:
        generate / replan a patch        (LLM, conditioned on prior failure)
        synthesize the diff              (edits.py)
        run the reproducer on the patch  (sandbox)
        GREEN -> stop.  RED -> feed the failure back and try again.

What makes it *search* and not retry: every attempt after the first is
conditioned on the concrete reproducer output from the previous one
(`_feedback` in loop.py), not re-rolled blind. What makes it safe: it is
bounded — unbounded retry on a noisy signal spends money without converging.

## Reward semantics (the honest part)

"Green" means the agent's own reproducer passed. That is a **proxy** for the
hidden graded tests, and it can be wrong both ways:
- false green — the reproducer passes for the wrong reason (**reproducer
  overfitting**); the real tests still fail;
- false red — the reproducer is stricter or buggier than the real tests.

The loop never sees the graded tests, by construction (firewall). The gap
between loop-green and harness-RESOLVED is a headline diagnostic that step 5
measures per instance.

## Built on two independently-validated engines

The loop is composition, so each piece under it was calibrated alone, with no
LLM, before wiring:

- **diff synthesizer** — `scripts/verify_sanitizer.py`: synthesizes the
  django__django-11099 fix byte-identical to the human gold patch, and real
  `git apply` accepts it. 4/4 cases (exact, indent-shift, bogus-raises,
  parse).
- **reproducer sandbox** — `scripts/verify_reproducer.py`: a hand-written
  reproducer for 11099 goes **RED with an empty patch and GREEN with the gold
  patch** inside the instance container. This is the inner-loop reward engine,
  calibrated the same way step 1 calibrated the grader — red proves it can see
  brokenness, green proves it recognizes a fix.

Container facts (probed, in `fixpoint/harness/sandbox.py`): repo at `/testbed`,
deps in the `testbed` conda env, base_commit reachable for a hard reset,
patches applied with the harness's own fallback chain (git apply → --reject →
patch --fuzz=5), patch + script passed base64 to avoid shell-quoting hazards.

## Status

The two engines are validated. The full loop (reproducer generation + replan)
needs live model calls to exercise end to end — pending API credits. Once
credits are topped up: run the loop over the subset, then compare
attempts-vs-resolve and loop-green-vs-RESOLVED against the single-shot
baseline.
