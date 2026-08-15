"""BM25 from scratch — the lexical retriever.

No rank_bm25 dependency: the algorithm is ~40 lines and every one is a design
decision worth owning. Validated against the hand trace in docs/RETRIEVAL.md
(gold file django/contrib/auth/validators.py, corpus of 820 files).

The score for a document D against query Q sums, over each unique query term t:

    IDF(t) * tf(t,D) * (k1 + 1) / (tf(t,D) + k1 * (1 - b + b * |D|/avgdl))

  IDF(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))   # Lucene variant

Three ideas: IDF makes rare terms count and common ones vanish; the k1 factor
saturates term frequency (the 4th "validator" is weaker than the 1st); the b
factor deflates long documents that would otherwise match everything by bulk.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Sequence

from fixpoint.retrieval.types import Document

# camelCase boundaries. Two rules because one regex can't see both edges:
#   _ACRONYM_END:  "HTTPServer"  -> "HTTP Server"  (acronym meets a word)
#   _WORD_CASE:    "fooBar"      -> "foo Bar"      (lower/digit meets upper)
_ACRONYM_END = re.compile(r"([A-Z]+)([A-Z][a-z])")
_WORD_CASE = re.compile(r"([a-z0-9])([A-Z])")


def tokenize(text: str) -> list[str]:
    """Split code + prose into terms. The SAME function must tokenize both
    documents and queries, or the scores compare apples to oranges.

    For each identifier-ish run we emit its word parts AND, when the run is a
    genuine compound, the intact identifier too. That intact token is the
    sniper: when an issue quotes an exact class name like
    "ASCIIUsernameValidator", the compound "asciiusernamevalidator" has df=1 —
    only the file defining it contains it — so it carries huge IDF. The parts
    (ascii, username, validator) still fire when the issue paraphrases. Emitting
    both is a deliberate double-count: exact-name matches SHOULD score twice.
    """
    toks: list[str] = []
    for ident in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text):
        spaced = _WORD_CASE.sub(r"\1 \2", _ACRONYM_END.sub(r"\1 \2", ident))
        parts = [p for p in re.findall(r"[a-z0-9]+", spaced.lower())
                 if len(p) > 1 and not p.isdigit()]  # drop 1-char and pure-number noise
        toks.extend(parts)
        # Only add the intact form when it actually differs from its parts, so
        # atomic tokens like "regex" aren't double-counted for no reason.
        intact = ident.replace("_", "").lower()
        if len(parts) > 1 and len(intact) > 1 and intact not in parts:
            toks.append(intact)
    return toks


class BM25Searcher:
    def __init__(self, docs: Sequence[Document], k1: float = 1.5, b: float = 0.75):
        self.docs = list(docs)
        self.k1 = k1
        self.b = b
        self.N = len(self.docs)

        # postings: token -> [(doc_index, term_freq)]. Building this instead of
        # a dense doc-term matrix is what makes lexical search fast: at query
        # time we only touch documents that actually contain a query term.
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.doc_len: list[int] = []
        df: dict[str, int] = defaultdict(int)
        for i, d in enumerate(self.docs):
            tf = Counter(tokenize(d.text))
            self.doc_len.append(sum(tf.values()))
            for tok, freq in tf.items():
                self.postings[tok].append((i, freq))
                df[tok] += 1  # +1 per document, so this IS document frequency

        self.avgdl = (sum(self.doc_len) / self.N) if self.N else 0.0
        # Precompute IDF once — it's per-term, not per-query, so recomputing it
        # inside search() would burn time for nothing.
        self.idf = {
            t: math.log(1 + (self.N - dfi + 0.5) / (dfi + 0.5))
            for t, dfi in df.items()
        }

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        if not self.docs:
            return []
        # Unique query terms, insertion order preserved (determinism; also a
        # repeated query word must not multiply its own contribution).
        q_terms = list(dict.fromkeys(tokenize(query)))

        scores: dict[int, float] = defaultdict(float)
        for t in q_terms:
            postings = self.postings.get(t)
            if not postings:  # query term absent from the whole corpus
                continue
            idf = self.idf[t]
            for i, freq in postings:
                length_norm = 1 - self.b + self.b * self.doc_len[i] / self.avgdl
                denom = freq + self.k1 * length_norm
                scores[i] += idf * freq * (self.k1 + 1) / denom

        # Ties broken by path so a fixed corpus always yields a fixed ranking —
        # the reproducibility our headline number depends on.
        ranked = sorted(scores.items(), key=lambda it: (-it[1], self.docs[it[0]].path))
        return [(self.docs[i].path, score) for i, score in ranked[:k]]
