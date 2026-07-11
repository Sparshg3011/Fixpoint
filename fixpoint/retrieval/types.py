"""Shared retrieval types — the contract between searchers and the eval harness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Document:
    """One candidate file. path is repo-relative (e.g. 'django/contrib/auth/validators.py')."""

    path: str
    text: str


class Searcher(Protocol):
    """Anything that ranks documents for a query.

    Constructed once per (repo, commit) corpus — put your index building in
    __init__, not in search(); the eval harness reuses one instance across ks.
    """

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        """Top-k (path, score), best first. Returning fewer than k is fine."""
        ...
