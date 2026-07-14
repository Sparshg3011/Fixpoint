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

## Single-shot run (n=25, Sonnet 5, 2026-07-14)

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
