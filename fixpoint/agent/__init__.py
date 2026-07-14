"""Agent side: turn an issue + candidate files into a unified diff that applies.

Firewall note: everything here is agent-side. It may import
fixpoint.bench.AgentView and nothing else from the grading side.
scripts/leak_audit.sh enforces it.
"""

from fixpoint.agent.edits import Edit, EditApplyError, parse_edits, synthesize_diff

__all__ = ["Edit", "EditApplyError", "parse_edits", "synthesize_diff"]
