"""File ranking = issue-mentioned paths first, BM25 for the rest.

Localization is the pipeline's hard cap: an instance whose buggy file never
reaches the model scores zero no matter how good the patch model is (measured
at 68% on Lite-300 with BM25 alone — a third of all instances lost before the
first token is generated).

The cheapest signal BM25 underuses: issue authors NAME the file. Tracebacks
carry `path/to/module.py` lines, and prose often cites the module directly.
A path that (a) appears verbatim in the issue and (b) uniquely resolves
against the actual repo tree is stronger evidence than any token statistic —
so those files go first, and BM25 fills the remaining slots.

Uniqueness is the safety valve: a bare `utils.py` matching twelve directories
identifies nothing and is ignored rather than guessed at.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from fixpoint.retrieval.bm25 import BM25Searcher
from fixpoint.retrieval.types import Document

# A python-file token as it appears in tracebacks, backticked prose, or bare
# text. Leading [\w] keeps a match from starting mid-punctuation; the body
# admits nested directories.
_PATH_TOKEN_RE = re.compile(r"[\w][\w./-]*\.py\b")

# The product flow runs on repos in any language, where the file an issue
# names is as likely `styles.css` as `views.py`. Same shape, any short
# extension — resolution against the real tree (below) is what keeps a stray
# "e.g" or "v2.1" from ever counting as a file.
_ANY_PATH_TOKEN_RE = re.compile(r"[\w][\w./-]*\.[A-Za-z][A-Za-z0-9]{0,7}\b")

# At most this many mention slots. The issue may cite many files (long
# tracebacks walk the whole call stack); the deepest signal is still worth at
# most a few of the k slots — BM25 keeps the rest.
_MENTION_CAP = 3


def mentioned_paths(text: str, corpus_paths: Iterable[str], cap: int = _MENTION_CAP,
                    any_extension: bool = False) -> list[str]:
    """Files the issue names that uniquely resolve in the corpus, in order of
    first appearance. `.py` tokens only by default — the definition the
    published benchmark numbers were measured with."""
    known = set(corpus_paths)
    hits: list[str] = []
    token_re = _ANY_PATH_TOKEN_RE if any_extension else _PATH_TOKEN_RE
    for token in token_re.findall(text):
        t = token.lstrip("./")
        if t in known:                      # full repo-relative path, verbatim
            matches = [t]
        else:                               # suffix form: `db/models/query.py`
            matches = [p for p in known if p.endswith("/" + t)]
        if len(matches) == 1 and matches[0] not in hits:
            hits.append(matches[0])
        if len(hits) >= cap:
            break
    return hits


def ranked_files(docs: list[Document], query: str, k: int,
                 any_extension: bool = False) -> list[str]:
    """Top-k candidate files: unique issue mentions first, BM25 after."""
    mentions = mentioned_paths(query, (d.path for d in docs), any_extension=any_extension)
    ranked = [p for p, _ in BM25Searcher(docs).search(query, k=k)]
    return (mentions + [p for p in ranked if p not in mentions])[:k]
