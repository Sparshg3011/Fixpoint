"""Mentions-first ranking: the issue naming a file beats token statistics —
but only when the name uniquely resolves against the real tree."""

from fixpoint.retrieval.rank import mentioned_paths, ranked_files
from fixpoint.retrieval.types import Document

PATHS = [
    "django/db/models/query.py",
    "django/forms/fields.py",
    "django/contrib/admin/fields.py",
    "django/utils/text.py",
]


def test_full_path_mention_wins():
    issue = "Traceback ... File \"django/db/models/query.py\", line 42, in filter"
    assert mentioned_paths(issue, PATHS) == ["django/db/models/query.py"]


def test_suffix_mention_resolves_when_unique():
    assert mentioned_paths("bug in utils/text.py somewhere", PATHS) == ["django/utils/text.py"]


def test_ambiguous_bare_filename_is_ignored():
    """`fields.py` matches two directories — identifying nothing, it must not guess."""
    assert mentioned_paths("the bug is in fields.py", PATHS) == []


def test_mentions_lead_and_bm25_fills():
    docs = [Document(path=p, text=t) for p, t in [
        ("pkg/a.py", "widget frobnicate widget"),
        ("pkg/b.py", "nothing relevant here"),
        ("pkg/c.py", "unrelated noise"),
    ]]
    ranked = ranked_files(docs, "widget breaks — see pkg/c.py", k=2)
    # c.py is mentioned (rank 1 despite zero BM25 signal); BM25's best fills slot 2.
    assert ranked == ["pkg/c.py", "pkg/a.py"]


def test_no_mentions_degrades_to_pure_bm25():
    docs = [Document(path="pkg/a.py", text="widget widget"),
            Document(path="pkg/b.py", text="other")]
    assert ranked_files(docs, "widget misbehaves", k=1) == ["pkg/a.py"]


def test_any_extension_mentions_serve_non_python_repos():
    """Product-flow repos are multi-language: a named stylesheet must rank
    first there, while the benchmark default stays .py-only."""
    paths = ["styles.css", "site/app.js", "site/index.html", "README.md"]
    issue = "The table in styles.css is too narrow, e.g. on v2.1 of the site."
    assert mentioned_paths(issue, paths) == []  # benchmark definition: .py only
    assert mentioned_paths(issue, paths, any_extension=True) == ["styles.css"]


# --- prose cap (product flow): docs read like the issue, code holds the bug ---

def _prose_heavy_docs():
    return [Document(path=p, text=t) for p, t in [
        ("docs/querysets.rst", "queryset none union empty form submission results " * 3),
        ("docs/forms.txt", "queryset none union empty form submission " * 3),
        ("CHANGES.md", "queryset none union empty form " * 3),
        ("orm/query.py", "def none(self): return self.union_empty()  # queryset none"),
        ("orm/forms.py", "class Field: queryset = None"),
    ]]


def test_prose_cap_keeps_code_in_the_top_k():
    issue = "queryset none on a union returns all results for an empty form submission"
    docs = _prose_heavy_docs()
    assert all(p.endswith((".rst", ".txt", ".md")) for p in ranked_files(docs, issue, k=3))
    capped = ranked_files(docs, issue, k=3, prose_cap=1)
    assert sorted(capped) == ["docs/querysets.rst", "orm/forms.py", "orm/query.py"]


def test_prose_cap_never_overrides_an_explicit_mention():
    issue = "typo in CHANGES.md and docs/forms.txt about queryset none union"
    ranked = ranked_files(_prose_heavy_docs(), issue, k=4, any_extension=True, prose_cap=1)
    assert ranked[:2] == ["CHANGES.md", "docs/forms.txt"]      # named, so exempt
    assert sum(p.endswith((".rst", ".txt", ".md")) for p in ranked[2:]) <= 1


def test_prose_cap_lets_docs_fill_a_repo_with_no_code():
    docs = [Document(path=f"notes/{n}.md", text=f"install guide step {n} widget") for n in range(4)]
    assert len(ranked_files(docs, "widget install guide", k=3, prose_cap=1)) == 3
