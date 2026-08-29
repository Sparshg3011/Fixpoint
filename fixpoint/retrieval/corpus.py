"""Source tree -> candidate Documents.

Candidates are non-test .py files. Two deliberate narrowings, both measurable:

  tests excluded   Lite gold patches never touch test files (the benchmark's
                   construction splits code and test changes), so test files
                   are pure haystack. If this assumption ever bites, recall@k
                   goes to zero for that instance and we see it immediately.
  size-capped      files beyond max_bytes are skipped: they are almost always
                   generated code or vendored bundles, and their sheer token
                   mass distorts corpus statistics like average doc length.
"""

from __future__ import annotations

from pathlib import Path

from fixpoint.retrieval.types import Document

# Any path segment matching these marks a test file in all 12 Lite repos
# (django tests/, sklearn */tests/, pytest testing/, etc.).
TEST_SEGMENTS = {"test", "tests", "testing"}


def is_test_path(rel_path: str) -> bool:
    parts = rel_path.split("/")
    if any(p in TEST_SEGMENTS for p in parts):
        return True
    name = parts[-1]
    return name.startswith("test_") or name.endswith("_test.py")


# What the product flow indexes for arbitrary repos. The benchmark keeps its
# .py-only default — the twelve Lite repos are Python and the published
# numbers were measured against that corpus definition — but a user's repo can
# be a static site, a Go service, anything. Text-ish source only; binaries and
# lockfiles stay out.
CODE_EXTENSIONS = (
    ".py", ".js", ".mjs", ".ts", ".jsx", ".tsx", ".vue", ".svelte",
    ".html", ".htm", ".css", ".scss", ".less",
    ".md", ".rst", ".txt", ".json", ".yml", ".yaml", ".toml", ".ini", ".xml",
    ".rb", ".go", ".rs", ".java", ".kt", ".swift", ".php",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".sh",
)


def load_corpus(tree: Path, include_tests: bool = False, max_bytes: int = 300_000,
                extensions: tuple[str, ...] = (".py",)) -> list[Document]:
    """All candidate files under tree with a matching suffix, sorted by path
    for determinism. Defaults to .py only — the benchmark corpus definition —
    with node_modules/.git-style vendored dirs always excluded."""
    skip_dirs = {".git", "node_modules", "vendor", "dist", "build", ".venv", "__pycache__"}
    docs: list[Document] = []
    for p in sorted(tree.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in extensions:
            continue
        rel = p.relative_to(tree).as_posix()
        if any(seg in skip_dirs for seg in rel.split("/")):
            continue
        if not include_tests and is_test_path(rel):
            continue
        if p.stat().st_size > max_bytes:
            continue
        # errors="replace": a handful of files carry non-UTF8 bytes in string
        # literals; mangling those is harmless for retrieval purposes.
        docs.append(Document(path=rel, text=p.read_text(errors="replace")))
    return docs
