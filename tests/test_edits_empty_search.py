"""Regression: an empty SEARCH must never silently corrupt a file.

Found by the test suite, not by review: `"" in content` is always True, so the
exact-match path matched instantly and `content.replace("", repl, 1)` prepended
the replacement to the top of the file — a silent corruption that would have
produced a diff git happily applies and that no one would have questioned.
"""

import pytest

from fixpoint.agent.edits import Edit, EditApplyError, synthesize_diff

FILE = "pkg/mod.py"
CONTENT = "import re\n\nx = 1\n"


@pytest.mark.parametrize("search", ["", "   ", "\n", "  \n  \n"])
def test_empty_or_whitespace_search_raises(search):
    with pytest.raises(EditApplyError, match="empty SEARCH"):
        synthesize_diff({FILE: CONTENT}, [Edit(FILE, search, "injected = True")])


def test_empty_search_never_injects_at_top_of_file():
    """The specific corruption: text appearing at line 1 out of nowhere."""
    try:
        diff = synthesize_diff({FILE: CONTENT}, [Edit(FILE, "", "injected = True")])
    except EditApplyError:
        return  # correct behaviour
    pytest.fail(f"silently produced a diff instead of raising:\n{diff}")
