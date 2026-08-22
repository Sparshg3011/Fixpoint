"""SWE-bench_Lite access with a hard visibility boundary.

Two types, on purpose:

    Instance   the full benchmark record. Importable by grading-side code only
               (harness/, eval/, scripts/).
    AgentView  the four fields the agent is allowed to see. Agent-side code
               imports this type and nothing else from this module.

Why so paranoid: resolution on SWE-bench is graded by hidden tests
(FAIL_TO_PASS / PASS_TO_PASS) delivered via a test_patch the agent must never
read. If any of those fields leak into a prompt, the headline number is
fiction. We enforce the boundary with types plus a mechanical grep audit
(scripts/leak_audit.sh) rather than reviewer discipline, because discipline
does not survive week two of a deadline.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from datasets import load_dataset

# The dataset moved orgs on the HF Hub (princeton-nlp -> SWE-bench). The old
# name still resolves and is the one every published paper cites, so we keep it.
DATASET_NAME = "princeton-nlp/SWE-bench_Lite"
VERIFIED_DATASET_NAME = "princeton-nlp/SWE-bench_Verified"

# Pinned the same way as the Lite fingerprint below: computed once from the
# split we first downloaded, then every later load must match it.
VERIFIED_FINGERPRINT = "a69f166d719a9ab119adb807bbc273c0c63a3b4361c56eced7b59053365ea4d7"

# sha256 over sorted "instance_id:base_commit" lines. Pins BOTH the task list
# and the exact code states we are graded against — "300 tasks" alone is not a
# reproducible claim. Every number we publish cites this hash, and load_lite()
# refuses to hand out a test split that doesn't match it.
EXPECTED_FINGERPRINT = "b4200a5b8fb441f25b5539ff84952fbd9d123f6c418935f3f3ef7cd26d4e015a"


@dataclass(frozen=True)
class Instance:
    """One benchmark task, grading-side view. Frozen: grading inputs are immutable."""

    instance_id: str  # e.g. "django__django-11099"
    repo: str  # e.g. "django/django"
    base_commit: str  # repo state the agent must patch
    environment_setup_commit: str  # commit the harness installs deps from
    version: str  # key into the harness's per-repo environment specs
    problem_statement: str  # the GitHub issue text — the only spec there is
    hints_text: str  # pre-fix issue comments; excluded from prompts by convention
    created_at: str
    # Upstream calls this field "patch". Renamed: the word "patch" will appear
    # all over agent-side code (model patches), and the leak audit needs one
    # unambiguous token to grep for. gold_patch is that token.
    gold_patch: str
    test_patch: str
    # Tuples rather than lists: immutable, so an Instance can never drift out
    # of agreement with the fingerprint after loading.
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]


@dataclass(frozen=True)
class AgentView:
    """Everything the agent may know about a task. Nothing else crosses the wall.

    Deliberately absent: version + environment_setup_commit (env plumbing the
    agent can't use anyway — default-deny), hints_text (inflates results vs.
    every published baseline), and all grading fields.
    """

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str


def _to_instances(rows) -> list[Instance]:
    return [
        Instance(
            instance_id=r["instance_id"],
            repo=r["repo"],
            base_commit=r["base_commit"],
            environment_setup_commit=r["environment_setup_commit"],
            version=r["version"],
            problem_statement=r["problem_statement"],
            hints_text=r["hints_text"],
            created_at=r["created_at"],
            gold_patch=r["patch"],
            test_patch=r["test_patch"],
            # Stored as JSON-encoded strings in the parquet, not lists — decode
            # here once so no caller ever discovers that surprise downstream.
            fail_to_pass=tuple(json.loads(r["FAIL_TO_PASS"])),
            pass_to_pass=tuple(json.loads(r["PASS_TO_PASS"])),
        )
        for r in rows
    ]


def _load_pinned(dataset_name: str, expected: str, split: str,
                 check_fingerprint: bool) -> list[Instance]:
    """Load a benchmark split and refuse to serve one that isn't the split we
    pinned — an unpinned dataset makes every published number unverifiable."""
    instances = _to_instances(load_dataset(dataset_name, split=split))
    if check_fingerprint and split == "test":
        actual = fingerprint(instances)
        if actual != expected:
            raise RuntimeError(
                f"{dataset_name} test split does not match the pinned fingerprint.\n"
                f"  expected {expected}\n"
                f"  actual   {actual}\n"
                "Numbers produced against this copy would not be comparable — refusing."
            )
    return instances


def load_lite(split: str = "test", check_fingerprint: bool = True) -> list[Instance]:
    """Load SWE-bench_Lite and verify it is byte-for-byte the split we pinned.
    (Only the 300-task test split carries the fingerprint; the 23-task dev
    split is scratch space and may be used freely.)"""
    return _load_pinned(DATASET_NAME, EXPECTED_FINGERPRINT, split, check_fingerprint)


def load_verified(split: str = "test", check_fingerprint: bool = True) -> list[Instance]:
    """SWE-bench_Verified: the human-filtered 500-instance benchmark the field
    actually competes on in 2026. Same schema, same firewall, own fingerprint."""
    return _load_pinned(VERIFIED_DATASET_NAME, VERIFIED_FINGERPRINT, split,
                        check_fingerprint)


def agent_view(inst: Instance) -> AgentView:
    """The single doorway between grading data and the agent."""
    return AgentView(
        instance_id=inst.instance_id,
        repo=inst.repo,
        base_commit=inst.base_commit,
        problem_statement=inst.problem_statement,
    )


def fingerprint(instances: Iterable[Instance]) -> str:
    """Order-independent hash of (task id, code state) pairs."""
    lines = sorted(f"{i.instance_id}:{i.base_commit}" for i in instances)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def get(instances: Sequence[Instance], instance_id: str) -> Instance:
    """Lookup by id with a loud failure — silent misses cost debugging hours."""
    for inst in instances:
        if inst.instance_id == instance_id:
            return inst
    raise KeyError(f"{instance_id!r} not found in the loaded split ({len(instances)} instances)")
