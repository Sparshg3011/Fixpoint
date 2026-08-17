"""Loop-bench resume semantics: rows survive a kill, but never cross experiments."""

import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_checkpoint_filters_by_k_and_attempts(tmp_path):
    lb = _load("run_loop_bench")
    p = tmp_path / "checkpoint.jsonl"
    row = {"instance_id": "r__r-1", "diff": "d", "loop_green": True}
    p.write_text(
        json.dumps({"k": 5, "attempts": 3, "row": row}) + "\n"
        + json.dumps({"k": 5, "attempts": 2, "row": {**row, "instance_id": "r__r-2"}}) + "\n"
        + '{"torn'  # mid-write kill must not poison the resume
    )
    assert lb.load_checkpoint(p, 5, 3) == {"r__r-1": row}
    assert lb.load_checkpoint(p, 5, 2) == {"r__r-2": {**row, "instance_id": "r__r-2"}}
    assert lb.load_checkpoint(p, 8, 3) == {}
    assert lb.load_checkpoint(tmp_path / "absent.jsonl", 5, 3) == {}
