"""Benchmark access. Agent code imports AgentView; everything else is grading-side."""

from fixpoint.bench.loader import (
    DATASET_NAME,
    EXPECTED_FINGERPRINT,
    VERIFIED_DATASET_NAME,
    VERIFIED_FINGERPRINT,
    AgentView,
    Instance,
    agent_view,
    fingerprint,
    get,
    load_lite,
    load_verified,
)

__all__ = [
    "DATASET_NAME",
    "EXPECTED_FINGERPRINT",
    "VERIFIED_DATASET_NAME",
    "VERIFIED_FINGERPRINT",
    "AgentView",
    "Instance",
    "agent_view",
    "fingerprint",
    "get",
    "load_lite",
    "load_verified",
]
