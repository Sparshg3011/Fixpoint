# Patching — design notes and the single-shot finding

Job: GitHub issue + retrieved files -> a unified diff that applies. The design
choice that carries the apply-rate is the **edit format**.

## Why SEARCH/REPLACE, not a raw diff

The model emits edit blocks — old text, new text, per file — never a unified
diff:

    path/to/file.py
    <<<<<<< SEARCH
    exact original lines
    =======
    replacement lines
    >>>>>>> REPLACE

We locate the SEARCH text in the real file and let `difflib` synthesize the
unified diff. Consequence: hunk headers (`@@ -a,b +c,d @@`) are correct by
construction. The single biggest source of `git apply` failures —
hallucinated line numbers — becomes structurally impossible instead of
something a regex has to repair. Matching two tiers: exact substring first,
then a uniform-indent-shift fallback; an unlocatable SEARCH raises
`EditApplyError` (a clean signal for the replan loop), never a silent mangle.

Offline proof: `python scripts/verify_sanitizer.py` synthesizes the fix for
django__django-11099 and confirms real `git apply` accepts it — the output is
byte-identical to the human gold patch.

## Single-shot run #2 (n=25, Sonnet 5, after the prompt fix)

Apply rate **33% -> 64%**. Every xml-tag failure is gone. What the run then
exposed is more interesting than the headline: apply rate (64%) came out
*exactly equal* to localization@5 (64%), and the contingency table shows why.

| | patch applied | failed |
|---|---|---|
| gold file retrieved | **15** | 1 |
| gold file missed | 1 | 8 |

**Given the right file, the patcher + sanitizer succeed 15/16 = 94%** — the
step-3 gate (>=90% of generated diffs apply) is met. The system-level 64% is
retrieval's recall propagating downstream, not a patching weakness.

The 9 failures split as 7 "no edit blocks" and 2 "targeted a file not shown",
and 8 of the 9 are retrieval misses. That is the model behaving *correctly*:
handed five files that don't contain the bug, it declines to invent an edit
rather than hallucinating a plausible-looking change into the wrong file. A
scaffold that scored higher here by forcing an edit every time would be worse,
not better — it would convert honest abstentions into wrong patches.

Consequence for where effort goes next: patch quality is not the bottleneck.
Localization is. Cost: $7.21 for 25 instances (~$0.29 each), 246s wall.

### Graded result: 9/25 = 36% RESOLVED

Official harness, unmodified, 923s to grade the 16 non-empty predictions
(9 empty patches are counted as unresolved without spending a container).
95% Wilson CI [20%, 55%] — n=25 is small; this is a subset estimate.

Funnel, which localizes exactly where instances are lost:

```
25 instances
 -> 16 retrieval found the gold file      (64%  recall@5)
 -> 15 of those produced an applying diff (94%  patcher+sanitizer)
 ->  9 of the applying diffs RESOLVED     (56%  conversion)
```

Two results worth keeping:

- **matplotlib__matplotlib-24149 resolved without retrieval finding the gold
  file.** The gold patch edits `lib/matplotlib/axes/_axes.py`; we edited
  `lib/matplotlib/__init__.py` and the hidden tests passed anyway. Grading is
  execution-based — matching the reference patch is not required, only making
  the tests pass. A diff-similarity metric would have scored this zero.
- **psf__requests-2674 did not resolve**, so the known network-flaky instance
  is not inflating the number.

The 7 applying-but-unresolved patches are the honest frontier: the model found
the right file and wrote something that applies, but it didn't fix the bug.
That is precisely the population the replan loop targets — it gets a second and
third attempt conditioned on the reproducer's failure output.

## Model comparison: same scaffold, same 25 instances

Every model below ran through the identical pipeline — same BM25 retrieval,
same prompt, same SEARCH/REPLACE synthesizer, graded by the same unmodified
harness. Only the model differs. NVIDIA NIM serves the open models free, so
the comparison cost nothing.

| model | apply rate | apply given the gold file was retrieved | cost (25) |
|---|---|---|---|
| Claude Sonnet 5 | 16/25 = 64% | **15/16 = 94%** | $7.21 |
| z-ai/glm-5.2 | 18/25 = 72% | **15/16 = 94%** | $0.00 |
| nvidia/llama-3.3-nemotron-super-49b | 1/25 = 4% | 1/16 = 6% | $0.00 |

The conditional rate is the honest measure of the patcher: **Sonnet 5 and
GLM-5.2 are indistinguishable at 94%** once retrieval does its job. Retrieval
is the shared ceiling (recall@5 = 64%), not the model.

### What the 49B model's 4% actually was

Worth recording because the first read was wrong. Two causes, one of them ours:

- **Our parser was too strict.** It emitted `>>>>>>> =======` as the divider
  and `>>>>>>> >>>>>>> REPLACE` as the terminator — unambiguous intent in
  syntax we rejected. Canonicalizing marker-only lines took parsing from 12/25
  to 16/25 (measured by replaying the saved raw responses offline, no re-run).
- **The model does not reproduce code verbatim.** It paraphrases and elides
  with `...` inside SEARCH blocks, which can never match. That is a genuine
  capability requirement of this edit format, and no parser fix helps.

Lesson: before concluding "the model can't do it", replay the raw responses
against a fixed parser. Half of this gap was our punctuation tolerance.

### GLM out-localized BM25

GLM-5.2's dominant failure was *"edit targets a file that was not provided"* —
5 of its 7 failures. In **2 of those 5 it named the exact gold file our
retriever had missed** (`django/contrib/admin/utils.py`,
`django/utils/numberformat.py`). The model knew where the bug was; we simply
hadn't handed it that file, so a correct patch was discarded.

That is a concrete, cheap next improvement: when the model requests a file
outside the retrieved set, fetch it and re-ask. On this subset it would
recover 2 instances (+8 points of apply rate) for one extra call.

## Single-shot run #1 (n=25, Sonnet 5, 2026-07-14)

First live run over the subset. The headline lesson is in the failure split:

| outcome | count | cause |
|---|---|---|
| applied cleanly | 8 | — |
| xml-tag path bug | 8 | prompt used `<path>` as a placeholder; model emitted literal `<path>...</path>` tags the parser mis-read |
| no edit blocks | 8 | same confusion cascaded into a different output shape |

**git-apply failures among successfully-parsed edits: 0.** The 33% apply rate
was entirely an output-FORMAT problem upstream of the diff mechanics — the
synthesizer never failed. Localization@5 was 62.5%, consistent with BM25
recall@5 = 64%, so retrieval was not the bottleneck here.

Fixes (both offline-validated, no API spend):
- Prompt: replaced the `<path>` placeholder with a bare-path instruction plus
  one concrete worked example. Angle-bracket placeholders invite literal tags.
- Parser: `_clean_path` strips `<path>`/`</path>`, backticks, and quotes, so the
  parser is robust regardless of prompt wording.
- Runs now record each model's raw response, so parser issues are debuggable
  offline instead of costing another run.

The apply-rate re-measurement (gate: >=90%) and the single-shot resolve rate
run next, once API credits are topped up.
