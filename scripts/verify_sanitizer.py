#!/usr/bin/env python
"""Prove the SEARCH/REPLACE -> diff synthesizer produces patches git accepts.

No LLM. We hand-write edit blocks that mirror the gold fix for
django__django-11099, synthesize a diff, and run the REAL `git apply` against a
throwaway repo built from the actual base-commit file. If git accepts it and
the resulting file matches the gold change, the sanitizer's core is sound.

Three cases: an exact-match edit (the happy path), an indent-shifted edit (the
whitespace fallback), and a non-existent SEARCH (must fail loudly, not
silently). Prints PASS/FAIL and exits nonzero on any failure.

    python scripts/verify_sanitizer.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fixpoint.agent.edits import Edit, EditApplyError, parse_edits, synthesize_diff
from fixpoint.retrieval import load_corpus, tree_at

REPO, COMMIT = "django/django", "d26b2424437dabeeca94d7900b37d2df4410da0c"
GOLD_FILE = "django/contrib/auth/validators.py"


def git_apply_ok(path_to_content: dict[str, str], diff: str) -> tuple[bool, str]:
    """Return (applied_cleanly, resulting_file_text) using real git apply."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for rel, content in path_to_content.items():
            f = root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        diff_file = root / "patch.diff"
        diff_file.write_text(diff)
        # --check first (the harness's own first apply command), then apply.
        check = subprocess.run(["git", "apply", "--check", "patch.diff"], cwd=root,
                               capture_output=True, text=True)
        if check.returncode != 0:
            return False, check.stderr
        subprocess.run(["git", "apply", "patch.diff"], cwd=root, check=True)
        return True, (root / GOLD_FILE).read_text()


def main() -> int:
    files = {d.path: d.text for d in load_corpus(tree_at(REPO, COMMIT))}
    original = files[GOLD_FILE]
    results: list[tuple[str, bool]] = []

    # --- case 1: exact-match edits mirroring the gold fix ($ -> \Z) ----------
    exact = [
        Edit(path=GOLD_FILE, search="    regex = r'^[\\w.@+-]+$'\n    message = _(\n        'Enter a valid username. This value may contain only English letters, ",
             replace="    regex = r'^[\\w.@+-]+\\Z'\n    message = _(\n        'Enter a valid username. This value may contain only English letters, "),
        Edit(path=GOLD_FILE, search="    regex = r'^[\\w.@+-]+$'\n    message = _(\n        'Enter a valid username. This value may contain only letters, ",
             replace="    regex = r'^[\\w.@+-]+\\Z'\n    message = _(\n        'Enter a valid username. This value may contain only letters, "),
    ]
    diff = synthesize_diff(files, exact)
    ok, result = git_apply_ok({GOLD_FILE: original}, diff)
    fixed = ok and "r'^[\\w.@+-]+\\Z'" in result and "r'^[\\w.@+-]+$'" not in result
    results.append(("exact match -> git apply", fixed))
    print("=== synthesized diff (case 1) ===")
    print(diff)

    # --- case 2: indent-shifted SEARCH (whitespace fallback) -----------------
    # Full lines, dedented to column 0. The file has them at 4-space indent, so
    # the fallback must find the block and shift the replacement back by +4.
    shifted = [Edit(path=GOLD_FILE,
                    search="regex = r'^[\\w.@+-]+$'\nmessage = _(",
                    replace="regex = r'^[\\w.@+-]+\\Z'\nmessage = _(")]
    try:
        diff2 = synthesize_diff(files, shifted)
        ok2, _ = git_apply_ok({GOLD_FILE: original}, diff2)
    except EditApplyError:
        ok2 = False
    results.append(("indent-shifted -> whitespace fallback applies", ok2))

    # --- case 3: bogus SEARCH must raise, not silently no-op -----------------
    bogus = [Edit(path=GOLD_FILE, search="this text is not in the file anywhere at all\n", replace="x\n")]
    try:
        synthesize_diff(files, bogus)
        raised = False
    except EditApplyError:
        raised = True
    results.append(("non-existent SEARCH raises EditApplyError", raised))

    # --- case 4: the parser round-trips the wire format ----------------------
    wire = f"{GOLD_FILE}\n<<<<<<< SEARCH\nabc\n=======\nxyz\n>>>>>>> REPLACE"
    parsed = parse_edits(wire)
    results.append(("parser reads one block", len(parsed) == 1 and parsed[0].path == GOLD_FILE))

    print("\n=== results ===")
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    passed = all(ok for _, ok in results)
    print(f"\nsanitizer: {'PASS' if passed else 'FAIL'} ({sum(ok for _, ok in results)}/{len(results)})")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
