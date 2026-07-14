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

## Numbers

Recall@k on n=25 lands in `data/retrieval/{mention,bm25}-n25.json` (gitignored;
regenerate with `python scripts/eval_retrieval.py --retriever bm25 --n 25`).
The README's retrieval line cites those files.
