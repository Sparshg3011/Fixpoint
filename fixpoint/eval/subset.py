"""Deterministic evaluation subsets.

Iterating on all 300 Lite instances is too slow and too expensive, so every
number before step 5 is measured on a fixed subset. Two properties matter:

  deterministic  no RNG anywhere — the same n always yields the same ids, so
                 numbers stay comparable across days and machines;
  representative proportional to the per-repo skew (django+sympy are 64% of
                 Lite), with every repo present so environment problems in
                 small repos surface early, and picks spread evenly through
                 each repo's id-sorted list so old and new library versions
                 both appear.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Sequence

from fixpoint.bench import Instance


def lite_subset(instances: Sequence[Instance], n: int = 25) -> list[Instance]:
    """Pick n instances: proportional per repo (floor 1), evenly spaced within."""
    ordered = sorted(instances, key=lambda i: i.instance_id)
    if n >= len(ordered):
        return ordered

    groups: dict[str, list[Instance]] = defaultdict(list)
    for inst in ordered:
        groups[inst.repo].append(inst)
    # Biggest repos first; name breaks ties so the order can never wobble.
    repos = sorted(groups, key=lambda r: (-len(groups[r]), r))

    if n <= len(repos):
        # Not enough budget for one-per-repo: cover the biggest n repos.
        alloc = {r: 1 for r in repos[:n]}
    else:
        total = len(ordered)
        raw = {r: len(groups[r]) * n / total for r in repos}
        alloc = {r: max(1, math.floor(raw[r])) for r in repos}
        # Largest-remainder repair loop: add to (or, if the floor-of-1 rule
        # overshot, remove from) repos until the allocation sums to exactly n.
        while sum(alloc.values()) < n:
            for r in sorted(repos, key=lambda r: (raw[r] - math.floor(raw[r]), r), reverse=True):
                if alloc[r] < len(groups[r]):
                    alloc[r] += 1
                    break
        while sum(alloc.values()) > n:
            for r in sorted(repos, key=lambda r: (raw[r] - math.floor(raw[r]), r)):
                if alloc[r] > 1:
                    alloc[r] -= 1
                    break

    picked: list[Instance] = []
    for r in repos:
        if r not in alloc:
            continue
        xs, k = groups[r], alloc[r]
        if k == 1:
            idxs = [len(xs) // 2]  # the middle beats the oldest issue
        else:
            # Linspace over the id-sorted list; dedupe (rounding can collide
            # on small repos) and top up from unused indices to keep exactly k.
            idxs = sorted({round(i * (len(xs) - 1) / (k - 1)) for i in range(k)})
            fill = (j for j in range(len(xs)) if j not in set(idxs))
            while len(idxs) < k:
                idxs.append(next(fill))
            idxs = sorted(idxs)
        picked.extend(xs[j] for j in idxs)
    return sorted(picked, key=lambda i: i.instance_id)
