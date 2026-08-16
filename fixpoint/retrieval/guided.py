"""Model-guided retrieval: let the model ask for the file it actually needs.

BM25 recall@5 = 64% is the measured ceiling on resolve rate — a bug in a file
we never showed the model cannot be fixed. The cheapest way past that ceiling
turned out to be the model itself: when it tries to edit a file we did not
provide, it is naming a localization hypothesis. On the n=25 subset, 2 of 5
such requests were the exact gold file BM25's top-5 had missed.

So instead of discarding that attempt, we resolve the requested path against
the real corpus and re-ask with the file included. Cost: one extra call, and
only for the ~20% of instances that ask — versus embedding every file in every
repo, which is ~50x the API traffic for a less direct signal.

Resolution is deliberately conservative. Models name files loosely ("python.py"
for "src/_pytest/python.py"), so we accept an exact path, or a unique basename
match, and refuse anything ambiguous — guessing between two same-named files
would feed the model the wrong source and waste the retry.
"""

from __future__ import annotations

from collections.abc import Iterable


def resolve_requested_paths(requested: Iterable[str], corpus: dict[str, str],
                            already_given: Iterable[str] = ()) -> dict[str, str]:
    """Map paths the model asked for onto real corpus files.

    Returns {path: content} for those that resolve unambiguously and were not
    already provided. Unresolvable or ambiguous requests are silently dropped —
    the caller simply has nothing new to add, which is the correct outcome.
    """
    have = set(already_given)
    resolved: dict[str, str] = {}
    for raw in requested:
        want = raw.strip().lstrip("./").strip()
        if not want or want in have or want in resolved:
            continue

        if want in corpus:  # exact repo-relative path
            resolved[want] = corpus[want]
            continue

        # Fall back to basename, but ONLY when it is unique in the corpus.
        # "utils.py" matches dozens of files in django; picking one at random
        # would send the model to the wrong source.
        base = want.split("/")[-1]
        matches = [p for p in corpus if p.split("/")[-1] == base]
        # Prefer a suffix match on the full requested path when the model gave
        # a partial path ("admin/utils.py") — more specific than basename alone.
        if len(matches) > 1 and "/" in want:
            narrowed = [p for p in matches if p.endswith("/" + want)]
            if len(narrowed) == 1:
                matches = narrowed
        if len(matches) == 1 and matches[0] not in have:
            resolved[matches[0]] = corpus[matches[0]]
    return resolved
