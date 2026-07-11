"""Agent-side retrieval: issue text in, ranked candidate files out.

Firewall note: nothing in this package may import grading fields. The only
benchmark inputs that exist here are AgentView-derived strings (repo,
base_commit, problem_statement). scripts/leak_audit.sh enforces this.
"""

from fixpoint.retrieval.checkout import tree_at
from fixpoint.retrieval.corpus import load_corpus
from fixpoint.retrieval.types import Document, Searcher

__all__ = ["Document", "Searcher", "load_corpus", "tree_at"]
