"""Chunk planning and disk accounting — the levers that make Lite-300 gradable
on a bounded Docker VM without re-downloading shared layers."""

from fixpoint.eval.chunking import parse_docker_size, plan_chunks

META = {
    "django__django-1": ("django/django", "3.0"),
    "django__django-2": ("django/django", "3.0"),
    "django__django-3": ("django/django", "3.1"),
    "sympy__sympy-1": ("sympy/sympy", "1.6"),
    "sympy__sympy-2": ("sympy/sympy", "1.6"),
}


def test_groups_by_repo_and_version():
    """Same env image => same chunk, so its layers download once."""
    chunks = plan_chunks(list(META), META, max_chunk=15)
    assert ["django__django-1", "django__django-2"] in chunks
    assert ["django__django-3"] in chunks
    assert ["sympy__sympy-1", "sympy__sympy-2"] in chunks


def test_is_deterministic_regardless_of_input_order():
    a = plan_chunks(list(META), META)
    b = plan_chunks(list(reversed(list(META))), META)
    assert a == b


def test_oversized_groups_split_at_max_chunk():
    meta = {f"i-{n}": ("r/r", "1.0") for n in range(40)}
    chunks = plan_chunks(list(meta), meta, max_chunk=15)
    assert [len(c) for c in chunks] == [15, 15, 10]


def test_unknown_instances_are_kept_not_dropped():
    """An id we can't classify still deserves grading."""
    chunks = plan_chunks(["mystery__x-1", "django__django-1"], META)
    flat = [i for c in chunks for i in c]
    assert "mystery__x-1" in flat and "django__django-1" in flat


def test_every_instance_appears_exactly_once():
    chunks = plan_chunks(list(META), META, max_chunk=2)
    flat = [i for c in chunks for i in c]
    assert sorted(flat) == sorted(META)


def test_parse_docker_sizes():
    assert parse_docker_size("57.25GB") == 57.25
    assert parse_docker_size("663MB") == 0.663
    assert parse_docker_size("24.58kB") == 2.458e-05
    assert parse_docker_size("1.2TB") == 1200.0
    assert parse_docker_size("garbage") == 0.0  # unreadable must not block grading
