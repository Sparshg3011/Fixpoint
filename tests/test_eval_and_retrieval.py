"""Subset determinism, recall accounting, and retrieval internals.

Determinism is load-bearing: every number we publish must be reproducible, so
the subset and the rankings must not wobble between runs.
"""

from fixpoint.agent.llm import _cost
from fixpoint.eval.recall import FILE_RE, first_hit_rank
from fixpoint.eval.subset import lite_subset
from fixpoint.retrieval.bm25 import BM25Searcher, tokenize
from fixpoint.retrieval.types import Document
from tests.test_bench_firewall import make


def instances(n_per_repo):
    out = []
    for repo, n in n_per_repo.items():
        for i in range(n):
            inst = make(instance_id=f"{repo}-{i:03d}", base_commit=f"c{i}")
            out.append(type(inst)(**{**inst.__dict__, "repo": repo}))
    return out


POP = instances({"django/django": 114, "sympy/sympy": 77, "psf/requests": 6, "pallets/flask": 3})


# --- subset -----------------------------------------------------------------

def test_subset_is_deterministic():
    assert [i.instance_id for i in lite_subset(POP, 25)] == [i.instance_id for i in lite_subset(POP, 25)]


def test_subset_size_is_exact():
    for n in (5, 12, 25, 40):
        assert len(lite_subset(POP, n)) == n


def test_subset_covers_every_repo_when_budget_allows():
    repos = {i.repo for i in lite_subset(POP, 25)}
    assert repos == {i.repo for i in POP}, "small repos must be represented"


def test_subset_is_proportional_to_skew():
    picked = lite_subset(POP, 25)
    counts = {r: sum(1 for i in picked if i.repo == r) for r in {i.repo for i in picked}}
    assert counts["django/django"] > counts["sympy/sympy"] > counts["psf/requests"]


def test_subset_larger_than_population_returns_all():
    assert len(lite_subset(POP, 10_000)) == len(POP)


def test_subset_smaller_than_repo_count_covers_biggest():
    picked = lite_subset(POP, 2)
    assert {i.repo for i in picked} == {"django/django", "sympy/sympy"}


# --- recall accounting ------------------------------------------------------

def test_gold_file_regex_reads_the_b_side_path():
    diff = "diff --git a/pkg/old.py b/pkg/new.py\n--- a/pkg/old.py\n+++ b/pkg/new.py\n"
    assert FILE_RE.findall(diff) == ["pkg/new.py"]


def test_first_hit_rank_is_one_based():
    assert first_hit_rank(["a.py", "b.py", "c.py"], ["c.py"]) == 3
    assert first_hit_rank(["a.py"], ["a.py"]) == 1


def test_first_hit_rank_returns_none_on_miss():
    assert first_hit_rank(["a.py", "b.py"], ["z.py"]) is None


def test_first_hit_rank_takes_the_earliest_gold_file():
    assert first_hit_rank(["a.py", "b.py"], ["b.py", "a.py"]) == 1


# --- tokenizer --------------------------------------------------------------

def test_tokenizer_splits_camel_case_and_keeps_the_compound():
    """The intact compound is the sniper token: df=1 for an exact class name."""
    toks = tokenize("ASCIIUsernameValidator")
    assert "ascii" in toks and "username" in toks and "validator" in toks
    assert "asciiusernamevalidator" in toks


def test_tokenizer_splits_snake_case():
    assert set(tokenize("get_user_name")) >= {"get", "user", "name"}


def test_tokenizer_drops_single_chars_and_pure_numbers():
    toks = tokenize("a bb 123 c4")
    assert "a" not in toks and "123" not in toks
    assert "bb" in toks


def test_tokenizer_is_shared_by_docs_and_queries():
    """Docs and queries must tokenize identically or scores are meaningless."""
    assert tokenize("UserValidator") == tokenize("UserValidator")


# --- BM25 -------------------------------------------------------------------

DOCS = [
    Document(path="b/match.py", text="username validator regex username"),
    Document(path="a/match.py", text="username validator regex username"),  # tie with above
    Document(path="c/other.py", text="completely unrelated content here"),
]


def test_bm25_ranks_relevant_doc_first():
    hits = BM25Searcher(DOCS).search("username validator", k=3)
    assert hits[0][0].endswith("match.py")


def test_bm25_ties_break_by_path_for_determinism():
    """Identical scores must order by path, or the headline number wobbles."""
    hits = BM25Searcher(DOCS).search("username validator", k=2)
    assert [p for p, _ in hits] == ["a/match.py", "b/match.py"]


def test_bm25_respects_k():
    assert len(BM25Searcher(DOCS).search("username", k=1)) == 1


def test_bm25_empty_corpus_returns_nothing():
    assert BM25Searcher([]).search("anything", k=5) == []


def test_bm25_query_with_no_corpus_overlap_returns_nothing():
    assert BM25Searcher(DOCS).search("zzzznotpresent", k=5) == []


# --- cost accounting --------------------------------------------------------

def test_cost_math_matches_the_price_table():
    # sonnet 5: $3/M in, $15/M out
    assert _cost("claude-sonnet-5", 1_000_000, 1_000_000) == 18.0
    assert _cost("claude-opus-4-8", 1_000_000, 0) == 5.0


def test_cost_of_unknown_model_is_zero_not_a_crash():
    assert _cost("some-future-model", 1000, 1000) == 0.0


def test_corpus_extensions_cover_non_python_repos(tmp_path):
    """A github.io-style repo (zero .py files) must still yield a corpus for
    the product flow — corpus_files=0 turned a real user run into two blind
    model calls. The benchmark default stays .py-only."""
    from fixpoint.retrieval.corpus import CODE_EXTENSIONS, load_corpus

    (tmp_path / "index.html").write_text("<h1>hi</h1>")
    (tmp_path / "style.css").write_text("h1 { color: red; }")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("vendored")

    assert load_corpus(tmp_path) == []  # benchmark default: .py only
    docs = load_corpus(tmp_path, extensions=CODE_EXTENSIONS)
    assert {d.path for d in docs} == {"index.html", "style.css"}  # vendored dir skipped
