"""Exercise 1 — BM25 from scratch (YOU implement this).

No rank_bm25 import: the whole algorithm is ~40 lines and every line is a
design decision you should own. Validate against the hand trace in the
session notes before trusting it.

The scoring function
--------------------
    score(D, Q) = sum over unique query terms t of:
        IDF(t) * tf(t,D) * (k1 + 1) / (tf(t,D) + k1 * (1 - b + b * len(D)/avgdl))

    IDF(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))     # Lucene variant:
             # always positive, so a term in >50% of docs can never *subtract*
             # evidence (the original Robertson IDF goes negative there).

  N       total docs; df(t) docs containing t; tf(t,D) occurrences of t in D;
  len(D)  tokens in D; avgdl mean len over the corpus.
  k1=1.5  term-frequency saturation; b=0.75 length normalization. Take these
          defaults; we ablate later only if recall says to.

Structures to build in __init__ (one pass over docs):
  - postings: dict[token, list[(doc_index, tf)]]  — also gives you df for free
  - doc_len: list[int], avgdl: float
  At search time, score ONLY docs that share >= 1 token with the query (union
  of the query tokens' postings). Scoring all ~2,700 docs per query works but
  hides the data structure that makes lexical search fast; do it properly.

The tokenizer is where the recall lives (this is the real exercise):
  - lowercase; split on non-alphanumerics ("contrib.auth.validators" ->
    contrib, auth, validators);
  - split snake_case and camelCase ("ASCIIUsernameValidator" -> ascii,
    username, validator + KEEP the intact compound "asciiusernamevalidator" —
    the compound is a near-unique token when the issue quotes an exact class
    name, and the parts still match when it paraphrases. Emitting both is a
    deliberate double-count: exact-name matches SHOULD score twice);
  - drop pure numbers and 1-character tokens.
  Document any further choice (stemming? stopwords? code keywords like "def",
  "self" are high-df — IDF already crushes them, measure before filtering) and
  keep the tokenizer a standalone function: the same one MUST tokenize docs
  and queries, or scores are garbage.

class BM25Searcher:
    __init__(self, docs: Sequence[Document], k1: float = 1.5, b: float = 0.75)
    search(self, query: str, k: int = 10) -> list[tuple[str, float]]
        # ties: break by path so results are deterministic
"""

from __future__ import annotations

from typing import Sequence

from fixpoint.retrieval.types import Document


def tokenize(text: str) -> list[str]:
    raise NotImplementedError("exercise 1 — the tokenizer is yours; spec above")


class BM25Searcher:
    def __init__(self, docs: Sequence[Document], k1: float = 1.5, b: float = 0.75):
        raise NotImplementedError("exercise 1 — see module docstring for the spec")

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        raise NotImplementedError
