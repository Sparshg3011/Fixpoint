"""Diff synthesizer: parsing, matching, and the diff we hand to git.

Hermetic — no network, no Docker, no API. These lock in the behaviours that
scripts/verify_sanitizer.py proves end-to-end against real `git apply`.
"""

import pytest

from fixpoint.agent.edits import Edit, EditApplyError, parse_edits, synthesize_diff

FILE = "pkg/mod.py"
CONTENT = "import re\n\n\nclass Thing:\n    regex = r'^[a-z]+$'\n    name = 'thing'\n"


def block(path, search, replace):
    return f"{path}\n<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE"


# --- parsing ---------------------------------------------------------------

def test_parses_clean_block():
    edits = parse_edits(block(FILE, "old", "new"))
    assert len(edits) == 1
    assert edits[0] == Edit(path=FILE, search="old", replace="new")


def test_parses_multiple_blocks():
    text = block(FILE, "a", "b") + "\n" + block("other.py", "c", "d")
    assert [e.path for e in parse_edits(text)] == [FILE, "other.py"]


@pytest.mark.parametrize("raw", [
    f"<path>{FILE}</path>",   # model wrapped the path in xml tags
    f"{FILE}</path>",         # stray closing tag (seen in a real run)
    f"`{FILE}`",              # backticked
    f'"{FILE}"',              # quoted
])
def test_strips_path_decoration(raw):
    """Regression: a real run emitted <path> tags and broke every edit."""
    edits = parse_edits(block(raw, "old", "new"))
    assert edits[0].path == FILE


def test_no_blocks_returns_empty():
    assert parse_edits("I think the bug is in mod.py, you should fix the regex.") == []


def test_block_with_decoration_only_path_is_skipped():
    assert parse_edits(block("</path>", "old", "new")) == []


# --- matching and synthesis -------------------------------------------------

def test_exact_match_produces_git_header_and_hunk():
    edits = [Edit(FILE, "    regex = r'^[a-z]+$'", "    regex = r'^[a-z]+\\Z'")]
    diff = synthesize_diff({FILE: CONTENT}, edits)
    assert diff.startswith(f"diff --git a/{FILE} b/{FILE}\n")
    assert f"--- a/{FILE}" in diff and f"+++ b/{FILE}" in diff
    assert "@@" in diff
    assert "-    regex = r'^[a-z]+$'" in diff
    assert "+    regex = r'^[a-z]+\\Z'" in diff
    assert diff.endswith("\n")


def test_indent_shifted_search_still_matches_and_preserves_indent():
    """The model dedented the code; we shift the replacement back by the delta."""
    edits = [Edit(FILE, "regex = r'^[a-z]+$'", "regex = r'^[a-z]+\\Z'")]
    diff = synthesize_diff({FILE: CONTENT}, edits)
    # Replacement must land at the file's real 4-space indent, not column 0.
    assert "+    regex = r'^[a-z]+\\Z'" in diff


def test_last_line_edit_keeps_trailing_newline():
    """Regression: the fallback used to strip a file's final newline."""
    content = "a = 1\nb = 2\n"
    edits = [Edit(FILE, "b = 2", "b = 3")]  # dedent-free but forces the fallback path
    out = synthesize_diff({FILE: content}, edits)
    assert "+b = 3" in out
    # A diff that silently drops the trailing newline would emit git's
    # "\ No newline at end of file" marker; assert we did not cause that.
    assert "\\ No newline at end of file" not in out


def test_multiple_edits_to_one_file_produce_one_file_diff():
    content = "x = 1\ny = 2\nz = 3\n"
    edits = [Edit(FILE, "x = 1", "x = 10"), Edit(FILE, "z = 3", "z = 30")]
    diff = synthesize_diff({FILE: content}, edits)
    assert diff.count(f"diff --git a/{FILE} b/{FILE}") == 1
    assert "+x = 10" in diff and "+z = 30" in diff


def test_no_net_change_yields_empty_diff():
    edits = [Edit(FILE, "    name = 'thing'", "    name = 'thing'")]
    assert synthesize_diff({FILE: CONTENT}, edits) == ""


# --- failure modes must be loud, never silent -------------------------------

def test_unlocatable_search_raises():
    with pytest.raises(EditApplyError, match="not found"):
        synthesize_diff({FILE: CONTENT}, [Edit(FILE, "def nonexistent():", "x")])


def test_edit_targeting_unknown_file_raises():
    with pytest.raises(EditApplyError, match="not among the provided files"):
        synthesize_diff({FILE: CONTENT}, [Edit("not/shown.py", "a", "b")])


def test_empty_search_raises():
    with pytest.raises(EditApplyError):
        synthesize_diff({FILE: CONTENT}, [Edit(FILE, "", "x")])


# --- cost accounting with prompt caching ------------------------------------

def test_cached_reads_are_a_tenth_of_input_price():
    from fixpoint.agent.llm import _cost
    full = _cost("claude-sonnet-5", 1_000_000, 0)
    cached = _cost("claude-sonnet-5", 0, 0, cache_read_tokens=1_000_000)
    assert cached == pytest.approx(full * 0.10)


def test_cache_writes_carry_the_25_percent_premium():
    from fixpoint.agent.llm import _cost
    full = _cost("claude-sonnet-5", 1_000_000, 0)
    written = _cost("claude-sonnet-5", 0, 0, cache_write_tokens=1_000_000)
    assert written == pytest.approx(full * 1.25)


# --- marker variants seen from non-Claude models -----------------------------

@pytest.mark.parametrize("divider,closer", [
    ("=======", ">>>>>>> REPLACE"),                 # canonical
    (">>>>>>> =======", ">>>>>>> >>>>>>> REPLACE"),  # observed from Nemotron
    ("=========", ">>>>>> REPLACE"),                 # length variation
])
def test_parses_marker_variants(divider, closer):
    """Models decorate conflict markers differently; intent is unambiguous, so
    the parser canonicalizes rather than rejecting the block."""
    text = f"{FILE}\n<<<<<< SEARCH\nold\n{divider}\nnew\n{closer}"
    edits = parse_edits(text)
    assert len(edits) == 1, f"failed to parse divider={divider!r} closer={closer!r}"
    assert edits[0].search == "old" and edits[0].replace == "new"


def test_canonicalizer_leaves_doctest_prompts_alone():
    """A docstring's >>> prompt must not be mistaken for a conflict marker."""
    content = 'def f():\n    """\n    >>> f()\n    1\n    """\n    return 1\n'
    edits = parse_edits(block(FILE, '    >>> f()\n    1', '    >>> f()\n    2'))
    diff = synthesize_diff({FILE: content}, edits)
    assert "+    >>> f()" in diff or "2" in diff


def test_canonicalizer_leaves_rst_underlines_alone():
    """A short === underline in a docstring is not a divider."""
    edits = parse_edits(block(FILE, "Title\n===\nbody", "Title\n===\nnew"))
    assert len(edits) == 1
    assert "===" in edits[0].search
