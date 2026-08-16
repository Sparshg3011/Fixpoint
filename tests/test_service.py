"""The product flow's input handling — the pieces that parse user input."""

import pytest

from fixpoint.service import _safe_id, normalize_repo


@pytest.mark.parametrize("raw,expected", [
    ("pallets/flask", "pallets/flask"),
    ("https://github.com/pallets/flask", "pallets/flask"),
    ("https://github.com/pallets/flask.git", "pallets/flask"),
    ("git@github.com:pallets/flask.git", "pallets/flask"),
    ("https://github.com/pallets/flask/issues/123", "pallets/flask"),
])
def test_normalize_repo(raw, expected):
    assert normalize_repo(raw) == expected


def test_normalize_repo_rejects_garbage():
    with pytest.raises(ValueError):
        normalize_repo("not a repo at all !!!")


def test_run_ids_are_filesystem_safe():
    assert "/" not in _safe_id("fix-django/django-123")
