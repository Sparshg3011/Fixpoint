# Retrieval — design notes and the hand trace

Job: issue text in, ranked candidate files out. Measured as recall@k against
gold-touched files on the deterministic n=25 subset. On Lite most gold patches
touch one file, so recall@k reads as "how often is THE file in the top k".

## Two searchers

**MentionSearcher (the baseline).** Pure string matching — no corpus stats. It
ranks files the issue text literally names, in two forms: filename mentions
(`validators.py`, `auth/forms.py`, traceback paths) and dotted module paths
(`contrib.auth.validators`). Score = number of distinct mentions a file
satisfies; ties broken by shorter path. This exists to set the bar: it is the
"issue named the file" signal in isolation, and BM25 has to beat it to justify
its complexity.

**BM25Searcher (the lexical retriever).** Okapi BM25 with the Lucene IDF
variant, k1=1.5, b=0.75. Built from scratch over a postings index so scoring
touches only documents sharing a query term. The tokenizer is the real work:
it splits camelCase/snake_case into parts AND keeps the intact compound
identifier, so an exact class-name quote scores twice (see below).

## Hand trace: django__django-11099

Corpus: django @ d26b242, 820 non-test .py files, avgdl ≈ 639 tokens. Query:
the full issue text (73 unique terms). Gold file `contrib/auth/validators.py`
is tiny (~70 tokens).

Per-term IDF and term-frequency in the gold file:

| term | df | IDF | tf in gold |
|---|---|---|---|
| validator | 17 | 3.85 | 4 |
| username | 27 | 3.40 | 4 |
| regex | 43 | 2.94 | 4 |
| newline | 6 | 4.84 | 0 |
| trailing | 23 | 3.55 | 0 |

One term by hand — `validator` in the gold file:
length factor = 0.25 + 0.75 · 70/639 = 0.332;
contribution = 3.85 · (4·2.5) / (4 + 1.5·0.332) = 3.85 · 2.22 = **8.56**.
With tf=1 it would be 6.42 — 4× the occurrences buys only 1.33× the score.
That is the k1 saturation term working.

The compound sniper: the tokenizer emits `asciiusernamevalidator` intact.
Its df is 1 — only the gold file defines that class — so its IDF is ~6.3, far
above any prose term. That single token is what pulls the tiny gold file up
past long, topically-adjacent files (`auth/models.py`) that accumulate points
across dozens of weak prose-term matches.

## Two failure mechanisms this exposes

1. **Breadth beats depth.** Score is a sum over matched terms, so a long file
   that legitimately contains a few query terms plus many incidental prose
   terms can outscore the short file that is actually about the bug. Levers:
   title-only / title-boosted queries (starve the prose matches), compound
   identifier tokens (add a high-IDF exact-match signal).
2. **Symptom vocabulary ≠ mechanism vocabulary.** The highest-IDF issue terms
   (`newline`, `trailing`) appear zero times in the buggy file, which contains
   `r'^[\w.@+-]+$'`. No lexical method crosses that gap — this is the hole
   dense embeddings are for (deferred to the hybrid step, adopted only if the
   miss analysis says it pays).

## Numbers (n=25 subset, 2026-07-14)

| retriever | recall@1 | recall@5 | recall@10 |
|---|---|---|---|
| mention (baseline) | 4% | 12% | 12% |
| BM25 | 20% | 64% | 72% |

The baseline's 12% is the free-localization rate: only ~1 in 8 Lite issues
names the gold file in a form pure string-matching catches. BM25's 64%@5 is a
5x lift — retrieval genuinely earns its place, which the low baseline is what
proves.

## Miss taxonomy (why BM25 misses 7 of 25 at k=10)

**Sibling / namesake confusion** — BM25 finds the right *area*, wrong file:
- django-15388: gold `template/autoreload.py`, BM25 #1 `utils/autoreload.py`
  (two files, same name; the bigger, more-referenced one wins).
- matplotlib-24149: gold `axes/_axes.py`, BM25 top has `axes/_base.py`.
- pylint-7114: gold `lint/expand_modules.py`, BM25 returns other `lint/*`.
Fusion won't fix these — they need within-file (chunk-level) signal or an LLM
disambiguating among siblings.

**Symptom vocabulary ≠ mechanism vocabulary** — the gold file shares no terms
with the issue's symptom language:
- pytest-6116: gold `_pytest/main.py` (generic name), missed even in an
  82-file corpus.
- sympy-21379: gold `core/mod.py`; the bug surfaces far from where Mod lives.
- django-17087: gold `db/migrations/serializer.py`.
This is the case FOR dense embeddings — but note embeddings do nothing for the
sibling problem above.

## Complementarity: the argument for fusion, in the data

django-16046 (gold `utils/numberformat.py`): the issue names the file, so the
mention baseline hits it at **rank 1**, but BM25 whiffs entirely (its top is
`docs/conf.py`, `admin/options.py` — long files winning on prose breadth).
A trivial "mention OR BM25@5" fusion recovers this one instance for free,
lifting recall@5 from 64% to 68%. RRF is the principled version; the point is
that the two signals miss *different* instances, which is exactly when fusion
pays.

## Mentions-first ranking shipped (2026-08-16, measured on Lite-300)

The fusion argument above got its full-scale test. `retrieval/rank.py` now
puts issue-mentioned paths first — a path token counts only if it resolves
UNIQUELY against the real tree (a bare `utils.py` matching twelve directories
identifies nothing and is ignored) — and BM25 fills the remaining slots.

Replayed offline over all 300 instances (stored BM25 rankings + gold files,
zero API calls):

| ranking | localization@5 |
|---|---|
| BM25 alone | 203/300 = 67.7% |
| mentions first, BM25 fill | **209/300 = 69.7%** |

36 instances had a resolvable mention; 6 flipped miss→hit and **0 flipped
hit→miss** — the uniqueness rule means a mention never displaces a gold file
that BM25 had already found. Strictly-positive changes at zero cost are rare;
this is one. The sibling-confusion and symptom-vocabulary classes above remain
open (they need chunk-level or dense signal).
