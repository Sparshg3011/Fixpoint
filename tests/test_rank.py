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
