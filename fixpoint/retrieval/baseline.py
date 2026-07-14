"""The free-localization baseline: rank files the issue text literally names.

This is not a real retriever — it's the *bar*. It answers one question: how
often does the issue hand us the buggy file for free by naming it? Whatever
recall@5 this scores, BM25 has to beat it, or the corpus statistics are
earning their keep. A hybrid that loses to grep-for-the-filename is a
regression dressed up as sophistication.

It uses zero corpus statistics: pure string matching of mention forms against
file paths. That's the point — it isolates the "issue named the file" signal
so we can price everything else against it.
"""

from __future__ import annotations

import re
from typing import Sequence

from fixpoint.retrieval.types import Document

# Filename mentions: "validators.py", "auth/forms.py", and the traceback form
# "django/contrib/auth/validators.py" (the trailing ", line 9" is not \w/./-
# so the match stops cleanly at ".py").
FILENAME_RE = re.compile(r"[\w/.-]+\.py")

# Dotted module paths: "contrib.auth.validators", "requests.exceptions". Start
# on a letter/underscore so we don't grab "3.0" or numeric version strings.
DOTTED_RE = re.compile(r"[A-Za-z_][\w.]{2,}")

# Mentions that match half the repo and localize nothing. Every package has an
# __init__.py and a setup.py; counting them as evidence just adds noise.
NOISE_BASENAMES = {"setup.py", "__init__.py", "conftest.py"}


class MentionSearcher:
    def __init__(self, docs: Sequence[Document]):
        self.docs = list(docs)

    def _candidates(self, query: str) -> set[tuple[str, str]]:
        """Distinct (kind, form) mentions in the query. A set, so a file named
        twice in two forms doesn't get to vote twice for itself."""
        cands: set[tuple[str, str]] = set()
        for raw in FILENAME_RE.findall(query):
            fn = raw.strip("./").lower()  # normalize "./validators.py" -> "validators.py"
            if fn.endswith(".py") and fn.split("/")[-1] not in NOISE_BASENAMES:
                cands.add(("file", fn))
        for raw in DOTTED_RE.findall(query):
            # Strip leading/trailing dots first: in prose a module is written
            # "...in contrib.auth.validators." — the sentence's period would
            # otherwise become a trailing slash and break the path match.
            raw = raw.strip(".")
            # Must be a genuine dotted path and not a filename (handled above).
            if "." in raw and not raw.endswith(".py"):
                cands.add(("mod", raw.replace(".", "/").lower()))
        return cands

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        cands = self._candidates(query)
        scored: list[tuple[int, int, str]] = []
        for d in self.docs:
            path = d.path.lower()
            # Score = how many distinct mentions this file satisfies. A file
            # matched by both its basename and its module path scores 2 and
            # rightly outranks one matched by a single loose mention.
            n = 0
            for kind, form in cands:
                if kind == "file":
                    # endswith("/"+form) matches on a path segment boundary so
                    # "views.py" hits "app/views.py" but not "previews.py";
                    # the == arm handles a fully-qualified mention.
                    if path.endswith("/" + form) or path == form:
                        n += 1
                elif form in path:  # module fragment as a path substring
                    n += 1
            if n:
                # sort key carries path length: a mention of "validators.py"
                # should prefer contrib/auth/validators.py over a deeper match.
                scored.append((n, len(d.path), d.path))
        scored.sort(key=lambda t: (-t[0], t[1], t[2]))
        return [(path, float(n)) for n, _, path in scored[:k]]
