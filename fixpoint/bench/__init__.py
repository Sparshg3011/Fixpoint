"""Benchmark access. Agent code imports AgentView; everything else is grading-side."""

from fixpoint.bench.loader import (
    DATASET_NAME,
    EXPECTED_FINGERPRINT,
    AgentView,
    Instance,
    agent_view,
    fingerprint,
    get,
    load_lite,
)

__all__ = [
    "DATASET_NAME",
    "EXPECTED_FINGERPRINT",
    "AgentView",
    "Instance",
    "agent_view",
    "fingerprint",
    "get",
    "load_lite",
]
