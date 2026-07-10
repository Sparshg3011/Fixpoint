"""Fixpoint: a SWE-bench coding agent.

Package layout mirrors the trust boundary, not just the pipeline:

    bench/      dataset access + the agent-visibility firewall
    harness/    Docker sandbox + official SWE-bench evaluation (grading side)
    retrieval/  issue text -> candidate files            (agent side, step 2)
    agent/      patcher, sanitizer, reproducer, loop     (agent side, steps 3-4)
    eval/       full runs, error taxonomy, reports       (grading side, step 5)

Rule that everything else hangs off: agent-side packages may import
`fixpoint.bench.AgentView` and nothing else from the grading side.
`scripts/leak_audit.sh` enforces this mechanically.
"""
