"""Grading-side retrieval eval: recall@k against gold-touched files.

This module is the ONLY place where retrieval output meets grading data.
Searchers get exactly two agent-visible strings — the problem statement as
the query and the base-commit tree as the corpus — and never see where the
gold patch pointed. We compare afterwards, out here.

recall@k = fraction of instances with at least one gold-touched file in the
top k. On Lite most gold patches touch exactly one file, so this reads as
"how often is THE file in the top k".
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from fixpoint.bench import Instance, agent_view
from fixpoint.retrieval import Document, Searcher, load_corpus, tree_at

# b-side of "diff --git a/<path> b/<path>" — the post-patch path, which is
# also correct for pure deletions in git's rename-less format.
FILE_RE = re.compile(r"^diff --git a/\S+ b/(\S+)$", re.MULTILINE)

SearcherFactory = Callable[[Sequence[Document]], Searcher]


def gold_files(inst: Instance) -> tuple[str, ...]:
    return tuple(dict.fromkeys(FILE_RE.findall(inst.gold_patch)))


def first_hit_rank(ranked_paths: Sequence[str], gold: Sequence[str]) -> int | None:
    """1-based rank of the first gold file in the ranking; None if absent."""
    gold_set = set(gold)
    for i, p in enumerate(ranked_paths, start=1):
        if p in gold_set:
            return i
    return None


def evaluate(factory: SearcherFactory, instances: Sequence[Instance],
             ks: Sequence[int] = (1, 5, 10)) -> dict:
    max_k = max(ks)
    rows = []
    for inst in instances:
        docs = load_corpus(tree_at(inst.repo, inst.base_commit))
        searcher = factory(docs)
        # The firewall in action: the query is AgentView data, nothing else.
        query = agent_view(inst).problem_statement
        ranked = [path for path, _ in searcher.search(query, k=max_k)]
        gold = gold_files(inst)
        rows.append({
            "instance_id": inst.instance_id,
            "gold": list(gold),
            "top": ranked[:max_k],
            "first_hit_rank": first_hit_rank(ranked, gold),
            "n_docs": len(docs),
        })
    hits = [r["first_hit_rank"] for r in rows]
    return {
        "n": len(rows),
        "recall": {k: sum(1 for h in hits if h is not None and h <= k) / len(rows) for k in ks},
        "hit_ranks": hits,
        "rows": rows,
    }
