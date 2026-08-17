"""Tier-3 fuzzy matching and whole-corpus edit-target resolution.

The fuzzy tier exists for one measured failure mode: the model reproduces the
right region but makes a typo-level transcription error (GLM Lite-300 replay:
`varname` for `self.varname`). It must NEVER fire on genuinely different code
or pick between two plausible sites — a wrong match silently replaces real
code, which is worse than no patch at all.
"""

import pytest

from fixpoint.agent.edits import Edit, EditApplyError, _apply_one, synthesize_diff

FILE = (
    "class Node:\n"
    "    def render(self, context):\n"
    "        if self.varname is None:\n"
    "            return ''\n"
    "        context[self.varname] = value\n"
    "        return value\n"
)


def test_fuzzy_rescues_typo_level_transcription():
    """The real rescued case: model dropped `self.` while copying a block.

    The typo has to be DILUTED by correct surrounding lines to clear the
    0.95 bar — which is the point: a large edit with one slip is rescuable,
    a tiny edit that is 8% wrong is not evidence of anything."""
    edit = Edit(path="f.py",
                search=("    def render(self, context):\n"
                        "        if varname is None:\n"          # <- missing self.
                        "            return ''\n"
                        "        context[self.varname] = value"),
                replace=("    def render(self, context):\n"
                         "        if self.varname is None:\n"
                         "            return url\n"
                         "        context[self.varname] = value"))
    out = _apply_one(FILE, edit)
    assert "return url" in out
    # The untouched neighbour lines survive verbatim.
    assert "        return value" in out


def test_fuzzy_rejects_typo_in_a_tiny_block():
    """Same typo, two-line block: 8% divergence is below the measured bar."""
    edit = Edit(path="f.py",
                search="        if varname is None:\n            return ''",
                replace="        return url")
    with pytest.raises(EditApplyError):
        _apply_one(FILE, edit)


def test_fuzzy_refuses_genuinely_different_code():
    edit = Edit(path="f.py",
                search="        while queue.pending():\n            drain(queue)",
                replace="        pass")
    with pytest.raises(EditApplyError):
        _apply_one(FILE, edit)


def test_fuzzy_refuses_ambiguous_sites():
    """Two equally-plausible non-overlapping candidates -> no guess, loud fail."""
    content = (
        "def alpha():\n"
        "    value = compute(x)\n"
        "    return value\n"
        "\n"
        "def beta():\n"
        "    value = compute(y)\n"
        "    return value\n"
    )
    edit = Edit(path="f.py",
                search="    value = compute(z)\n    return value",
                replace="    return compute(z)")
    with pytest.raises(EditApplyError):
        _apply_one(content, edit)


def test_corpus_resolves_a_file_the_model_was_never_shown():
    """An edit may target a real repo file outside the shown set — the SEARCH
    text must still match that file's actual content."""
    edits = [Edit(path="other.py", search="x = 1", replace="x = 2")]
    diff = synthesize_diff({"shown.py": "y = 0\n"}, edits, corpus={"other.py": "x = 1\n"})
    assert "diff --git a/other.py b/other.py" in diff
    assert "+x = 2" in diff


def test_unknown_path_still_fails_with_corpus():
    edits = [Edit(path="ghost.py", search="x", replace="y")]
    with pytest.raises(EditApplyError, match="not among the provided"):
        synthesize_diff({"shown.py": "x\n"}, edits, corpus={"other.py": "x\n"})
