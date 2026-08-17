"""SEARCH/REPLACE edits -> a git-applyable unified diff.

This is the sanitizer's heart. The model never writes a unified diff; it emits
edit blocks in this exact format (one per change), which we parse and turn into
a diff ourselves:

    path/to/file.py
    <<<<<<< SEARCH
    the exact original lines
    =======
    the replacement lines
    >>>>>>> REPLACE

Why this shape wins on apply-rate: the model's only job is to reproduce a chunk
of existing code and say what it becomes. It never invents `@@ -a,b +c,d @@`
headers or counts lines — the single biggest source of `git apply` failures.
We locate the SEARCH text in the real file and let difflib compute the hunk
with correct line numbers and context. Malformed-line-number failures become
structurally impossible instead of something a regex has to repair.

What can still fail here — and is handled explicitly — is the *match*: the
model's SEARCH text must be found in the file. Exact match first; a
whitespace-tolerant fallback (leading-indent normalization) catches the common
case where the model gets the code right but the indentation slightly wrong.
Anything the fallback can't place raises EditApplyError, which the replan loop
(step 4) will read and correct — a clean failure the agent can act on, not a
silently mangled patch.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# One capturing regex for the whole block. re.DOTALL so SEARCH/REPLACE bodies
# may span many lines; the path is whatever sits on the line above the fence.
_BLOCK_RE = re.compile(
    r"(?P<path>[^\n]+?)\n"
    r"<{5,}\s*SEARCH\s*\n"
    r"(?P<search>.*?)"
    r"\n={5,}\s*\n"
    r"(?P<replace>.*?)"
    r"\n>{5,}\s*REPLACE",
    re.DOTALL,
)


@dataclass(frozen=True)
class Edit:
    """One located change: replace `search` with `replace` in `path`."""

    path: str
    search: str
    replace: str


class EditApplyError(Exception):
    """A SEARCH block could not be located in its file. Carries enough context
    for the replan loop to tell the model exactly what went wrong."""


# Models sometimes echo a <path>...</path> wrapper (especially if the prompt
# ever used <path> as a placeholder). Strip it defensively so the parser is
# robust regardless of prompt wording.
_PATH_TAG_RE = re.compile(r"</?\s*path\s*>", re.IGNORECASE)


def _clean_path(raw: str) -> str:
    """Reduce the path line to a bare repo-relative path.

    Models decorate this line in predictable ways: xml tags, backticks, quotes,
    and — because the prompt presents each file under a `## <path>` heading —
    the markdown heading marker itself. All are stripped; the intent is never
    ambiguous.
    """
    p = _PATH_TAG_RE.sub("", raw)   # drop <path> / </path>
    p = p.strip().strip("`").strip()  # drop code-fence backticks
    p = p.strip('"').strip("'").strip()  # drop stray quotes
    p = p.lstrip("#").lstrip("-").lstrip("*").strip()  # drop markdown heading/list markers
    return p


# Different models decorate the conflict markers differently. Observed in real
# runs: ">>>>>>> =======" used as the divider, ">>>>>>> >>>>>>> REPLACE" as the
# terminator, and "<<<<<< SEARCH" with six brackets instead of seven. The
# INTENT is unambiguous in every case, so we canonicalize marker-only lines
# before parsing rather than rejecting the block. Judging a model on our
# tolerance for its punctuation would measure the wrong thing.
_MARK = r"[<>=]{4,}"
_OPEN_RE = re.compile(rf"^(?:{_MARK}\s*)+SEARCH\s*$")
_CLOSE_RE = re.compile(rf"^(?:{_MARK}\s*)+REPLACE\s*$")
_DIVIDER_RE = re.compile(rf"^(?:{_MARK}\s*)+$")


def _canonicalize_markers(text: str) -> str:
    """Rewrite marker-only lines to the canonical form the block regex expects.

    Only lines consisting ENTIRELY of markers (optionally with SEARCH/REPLACE)
    are touched, and 4+ marker characters are required — so a docstring's `>>>`
    doctest prompt or a short `===` rule is left alone.
    """
    out = []
    for line in text.splitlines():
        s = line.strip()
        if _OPEN_RE.match(s):
            out.append("<<<<<<< SEARCH")
        elif _CLOSE_RE.match(s):
            out.append(">>>>>>> REPLACE")
        elif _DIVIDER_RE.match(s):
            out.append("=======")
        else:
            out.append(line)
    return "\n".join(out)


def parse_edits(text: str) -> list[Edit]:
    """Pull every SEARCH/REPLACE block out of a model response."""
    edits: list[Edit] = []
    for m in _BLOCK_RE.finditer(_canonicalize_markers(text)):
        path = _clean_path(m.group("path"))
        if path:  # skip a block whose path line was pure decoration
            edits.append(Edit(path=path, search=m.group("search"), replace=m.group("replace")))
    return edits


def _indent_len(line: str) -> int:
    """Number of leading whitespace characters on a line."""
    return len(line) - len(line.lstrip())


# Minimum similarity for the tier-3 fuzzy match, measured — not guessed — by
# replaying the GLM Lite-300 run's failed responses at several thresholds
# (scripts/rescue_parse.py). High enough that a match means "the model made a
# typo-level transcription error", not "this code looks vaguely similar":
# a wrong fuzzy match would silently replace REAL code the model never read.
FUZZY_THRESHOLD = 0.95

# A competing window scoring this close to the best is treated as ambiguity —
# two near-identical candidate sites mean we cannot know which one the model
# meant, and guessing between them is how silent corruption happens.
_FUZZY_AMBIGUITY_MARGIN = 0.02


def _fuzzy_window(file_lines: list[str], search_lines: list[str]) -> int | None:
    """Best fuzzy match of the search block against same-length line windows.

    Returns the starting index, or None when nothing clears FUZZY_THRESHOLD or
    the best match is ambiguous. Comparison is on stripped lines (indentation
    is tier 2's business); difflib's ratio cascade keeps the scan cheap.
    """
    n = len(search_lines)
    if n == 0 or len(file_lines) < n:
        return None
    target = "\n".join(ln.strip() for ln in search_lines)
    norm = [ln.strip() for ln in file_lines]

    scores: list[tuple[float, int]] = []
    matcher = difflib.SequenceMatcher(autojunk=False)
    matcher.set_seq2(target)
    for i in range(len(file_lines) - n + 1):
        window = "\n".join(norm[i:i + n])
        matcher.set_seq1(window)
        # Cheap upper bounds first; the full quadratic ratio only runs on
        # windows that could plausibly clear the threshold.
        if matcher.real_quick_ratio() < FUZZY_THRESHOLD:
            continue
        if matcher.quick_ratio() < FUZZY_THRESHOLD:
            continue
        score = matcher.ratio()
        if score >= FUZZY_THRESHOLD:
            scores.append((score, i))
    if not scores:
        return None

    scores.sort(reverse=True)
    best_score, best_i = scores[0]
    # Ambiguous iff a NON-overlapping window comes within the margin — windows
    # overlapping the winner share most of its lines and are echoes, not rivals.
    for score, i in scores[1:]:
        if abs(i - best_i) >= n and score >= best_score - _FUZZY_AMBIGUITY_MARGIN:
            return None
    return best_i


def _apply_one(content: str, edit: Edit) -> str:
    """Return `content` with the first occurrence of edit.search replaced.

    Two tiers, deliberately ordered:
      1. exact substring match — the common, unambiguous case;
      2. indent-shift match — the model reproduced the code and its relative
         structure but got the absolute indentation uniformly wrong (it re-typed
         from context and dropped or added a level). We match on stripped lines,
         then shift the replacement by the indent DELTA of the matched block so
         relative indentation is preserved — not flattened.
    A no-match raises rather than guessing, so the replan loop gets a real
    signal instead of a silently wrong patch.

    The fallback is deliberately conservative: it only corrects a *uniform*
    indent offset. Non-uniform indentation errors and near-miss text still
    fail here — that's the frontier a fuzzier matcher (difflib ratio, token
    alignment) would push back, and where the resolve rate says whether it's
    worth the added false-match risk.
    """
    # Reject an empty/whitespace-only SEARCH before anything else: "" is a
    # substring of every string, so the exact-match path below would "match"
    # instantly and content.replace("", ...) would silently prepend the
    # replacement to the top of the file. A malformed block must fail loudly,
    # never corrupt the file.
    if not edit.search.strip():
        raise EditApplyError(f"empty SEARCH block for {edit.path!r}")

    if edit.search in content:
        return content.replace(edit.search, edit.replace, 1)

    file_lines = content.splitlines(keepends=True)
    search_lines = edit.search.splitlines()
    norm = [ln.strip() for ln in search_lines]
    n = len(norm)

    # Tier 2: exact match on stripped lines (uniform indent drift).
    match_i = next((i for i in range(len(file_lines) - n + 1)
                    if [file_lines[j].strip() for j in range(i, i + n)] == norm), None)
    # Tier 3: fuzzy window match (typo-level transcription errors). Measured on
    # the GLM Lite-300 replay before shipping — see FUZZY_THRESHOLD.
    if match_i is None:
        match_i = _fuzzy_window(file_lines, search_lines)
    if match_i is None:
        raise EditApplyError(
            f"SEARCH block not found in {edit.path!r}. First line was:\n"
            f"  {edit.search.splitlines()[0] if edit.search.splitlines() else '<empty>'!r}"
        )

    i = match_i
    # Indent delta from the first non-blank matched line vs its search line.
    k = next((idx for idx in range(n) if norm[idx]), 0)
    delta = _indent_len(file_lines[i + k]) - _indent_len(search_lines[k])
    before = "".join(file_lines[:i])
    after = "".join(file_lines[i + n:])
    adjusted = []
    for ln in edit.replace.splitlines():
        if not ln.strip():
            adjusted.append(ln)  # leave blank lines blank
        elif delta >= 0:
            adjusted.append(" " * delta + ln)  # add missing indent
        else:
            adjusted.append(ln[min(-delta, _indent_len(ln)):])  # remove extra indent
    new_block = "\n".join(adjusted)
    # Add a trailing newline if the replacement text carries one, if there
    # is content after the match, or if the last replaced FILE line ended in
    # one — so editing a file's final line doesn't strip its final newline.
    if edit.replace.endswith("\n") or after or file_lines[i + n - 1].endswith("\n"):
        new_block += "\n"
    return before + new_block + after


def synthesize_diff(files: dict[str, str], edits: Sequence[Edit],
                    corpus: Mapping[str, str] | None = None) -> str:
    """Apply edits to the given file contents and emit one git unified diff.

    `files` maps repo-relative path -> current file content (the agent reads
    these from the checkout). We compute post-edit content per file and diff it
    against the original with difflib, so every hunk header is correct by
    construction. Files with no net change contribute nothing.

    `corpus` (path -> content for the WHOLE repo at base commit) lets an edit
    target a real file the model was never shown. Models know these codebases
    from training and routinely write from-memory edits to the right file; the
    SEARCH text must still match the actual content, so a hallucinated file
    body fails exactly as before. Measured on the GLM Lite-300 replay: 39
    instances died solely on the shown-files restriction. No firewall concern —
    the corpus is the agent-visible checkout, never anything grading-side.
    """
    # Group edits by path so multiple edits to one file produce one file diff.
    by_path: dict[str, list[Edit]] = {}
    for e in edits:
        by_path.setdefault(e.path, []).append(e)

    chunks: list[str] = []
    for path, path_edits in by_path.items():
        if path not in files and corpus is not None and path in corpus:
            files = {**files, path: corpus[path]}
        if path not in files:
            raise EditApplyError(f"edit targets {path!r}, which was not among the provided files")
        original = files[path]
        modified = original
        for e in path_edits:
            modified = _apply_one(modified, e)
        if modified == original:
            continue

        # keepends so difflib preserves exact line boundaries; lineterm='' so it
        # doesn't add its own — we control newlines via the kept ends.
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,  # 3 lines of context — git apply's default expectation
            lineterm="",
        )
        body = "".join(_ensure_newline(line) for line in diff)
        # The `diff --git` line is what git apply keys file identity on; the
        # ---/+++ lines difflib already produced sit inside `body`.
        chunks.append(f"diff --git a/{path} b/{path}\n{body}")

    return "".join(chunks)


def _ensure_newline(line: str) -> str:
    """difflib emits header lines (---, +++, @@) without a trailing newline when
    lineterm='' ; content lines keep their own. Normalize every emitted line to
    end in exactly one newline so the assembled diff is well-formed."""
    return line if line.endswith("\n") else line + "\n"
