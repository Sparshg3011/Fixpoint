"""Resolve an Instance to its prebuilt container image key.

Grading-side: uses the harness's own naming (via make_test_spec) so the image
string is identical to what the official harness pulls — no hand-rolled `__` ->
`_1776_` mangling to drift out of sync.
"""

from __future__ import annotations

from swebench.harness.test_spec.test_spec import make_test_spec

from fixpoint.bench import Instance


def image_key(inst: Instance, namespace: str = "swebench") -> str:
    # make_test_spec wants the full row schema; the grading fields are unused
    # for image naming, so we pass empty placeholders rather than real values.
    row = {
        "instance_id": inst.instance_id, "repo": inst.repo, "base_commit": inst.base_commit,
        "patch": "", "test_patch": "", "problem_statement": "", "hints_text": "",
        "created_at": inst.created_at, "version": inst.version,
        "FAIL_TO_PASS": "[]", "PASS_TO_PASS": "[]",
        "environment_setup_commit": inst.environment_setup_commit,
    }
    return make_test_spec(row, namespace=namespace).instance_image_key
