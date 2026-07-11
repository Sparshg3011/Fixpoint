"""Exercise 0 — the free-localization baseline (YOU implement this).

Question this searcher answers: how often does the issue text literally name
the buggy file? Whatever recall@5 this scores is the bar BM25 must beat —
a fancy retriever that loses to grep-for-the-filename is a net negative.

Spec
----
class MentionSearcher:
    __init__(self, docs: Sequence[Document])
    search(self, query: str, k: int = 10) -> list[tuple[str, float]]

Ranking rule (keep it dumb on purpose):
  1. Extract mention candidates from the query:
       a. filename tokens:  r"[\\w/.-]+\\.py"   ("validators.py", "auth/forms.py")
       b. dotted module paths: r"[A-Za-z_][\\w.]{2,}" filtered to those containing
          a dot and no ".py" ("contrib.auth.validators") — convert dots to "/".
  2. A doc matches a candidate if its path ends with the filename form, or
     contains the module form as a path substring.
  3. Score = number of distinct candidates a doc matches; ties broken by
     shorter path (a mention of "validators.py" should prefer
     contrib/auth/validators.py over something deeply vendored).
  4. Return only matching docs, best first. Fewer than k results is fine —
     an empty list just means the issue named nothing.

Gotchas worth thinking about (they cost recall if ignored):
  - the same file is often mentioned twice in different forms — dedupe candidates;
  - "setup.py" and "__init__.py" mentions are usually noise; decide and document;
  - module paths in tracebacks ("django/contrib/auth/validators.py", line 9)
    arrive with line-number suffixes — your regexes above already handle this,
    check that they actually do.
"""

from __future__ import annotations

from typing import Sequence

from fixpoint.retrieval.types import Document


class MentionSearcher:
    def __init__(self, docs: Sequence[Document]):
        raise NotImplementedError("exercise 0 — see module docstring for the spec")

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        raise NotImplementedError
