"""Grading-side execution: the official SWE-bench harness, wrapped thinly."""

from fixpoint.harness.official import (
    CALIB_DIR,
    NOOP_PATCH,
    read_instance_report,
    run_official_eval,
    summarize_report,
    write_predictions,
)

__all__ = [
    "CALIB_DIR",
    "NOOP_PATCH",
    "read_instance_report",
    "run_official_eval",
    "summarize_report",
    "write_predictions",
]
