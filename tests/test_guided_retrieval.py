"""Model-guided retrieval: resolving a file the model asked for.

The model naming a file we did not provide is a localization hypothesis, and
measured on the n=25 subset 2 of 5 such requests were the exact gold file. But
resolution must be conservative: feeding back the WRONG same-named file wastes
the retry and can send the model further astray.
"""

from fixpoint.retrieval.guided import resolve_requested_paths

CORPUS = {
    "django/contrib/admin/utils.py": "admin utils",
    "django/db/models/utils.py": "db utils",
    "django/utils/numberformat.py": "numberformat",
    "src/_pytest/python.py": "pytest python",
}


def test_exact_path_resolves():
    got = resolve_requested_paths(["django/utils/numberformat.py"], CORPUS)
    assert got == {"django/utils/numberformat.py": "numberformat"}


def test_unique_basename_resolves():
    """Models name files loosely: 'python.py' for 'src/_pytest/python.py'."""
    got = resolve_requested_paths(["python.py"], CORPUS)
    assert got == {"src/_pytest/python.py": "pytest python"}


def test_ambiguous_basename_is_refused():
    """'utils.py' matches two files — guessing would feed the wrong source."""
    assert resolve_requested_paths(["utils.py"], CORPUS) == {}


def test_partial_path_disambiguates_a_shared_basename():
    got = resolve_requested_paths(["admin/utils.py"], CORPUS)
    assert got == {"django/contrib/admin/utils.py": "admin utils"}


def test_unknown_file_resolves_to_nothing():
    """A hallucinated filename must not crash or invent content."""
    assert resolve_requested_paths(["does/not/exist.py"], CORPUS) == {}


def test_already_provided_files_are_not_re_added():
    got = resolve_requested_paths(["django/utils/numberformat.py"], CORPUS,
                                  already_given=["django/utils/numberformat.py"])
    assert got == {}


def test_leading_dot_slash_is_tolerated():
    got = resolve_requested_paths(["./django/utils/numberformat.py"], CORPUS)
    assert got == {"django/utils/numberformat.py": "numberformat"}


def test_singleshot_module_imports():
    """Guard: a defaulted dataclass field placed before non-default ones raises
    TypeError at import. Nothing else in the suite imports this module, so the
    breakage was invisible until a run crashed."""
    import fixpoint.eval.singleshot as ss
    assert ss.SingleShotResult is not None


def test_all_package_modules_import():
    """Every module must import cleanly — cheap insurance against the above."""
    import importlib
    import pkgutil

    import fixpoint
    for m in pkgutil.walk_packages(fixpoint.__path__, "fixpoint."):
        importlib.import_module(m.name)
