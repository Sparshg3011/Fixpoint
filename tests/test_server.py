"""The web API — a pure renderer's data source, discovered from disk.

Tests point the module at a synthetic artifacts directory: nothing here
depends on real benchmark data, and nothing in the server may hardcode any.
"""

import json

import pytest
from fastapi.testclient import TestClient

import fixpoint.server as srv


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "singleshot" / "modelx").mkdir(parents=True)
    (tmp_path / "singleshot" / "modelx" / "results.json").write_text(json.dumps({
        "model": "vendor/modelx", "n": 4, "total_cost_usd": 0.0, "wall_s": 60,
        "rows": [
            {"instance_id": "r__r-1", "applied": True, "gold_retrieved_rank": 1,
             "diff": "diff --git a/f b/f\n", "error": None, "top_files": ["f"],
             "gold_files": ["f"], "cost_usd": 0, "input_tokens": 1, "output_tokens": 1},
            {"instance_id": "r__r-2", "applied": False, "gold_retrieved_rank": None,
             "diff": "", "error": "no edit blocks", "top_files": [], "gold_files": ["g"],
             "cost_usd": 0, "input_tokens": 1, "output_tokens": 1},
        ]}))
    (tmp_path / "singleshot" / "modelx" / "graded.json").write_text(json.dumps({
        "model": "modelx", "total_predictions": 4, "empty": 2, "graded": 1,
        "resolved": 1, "per_instance": {"r__r-1": True}}))
    # A generated-but-never-graded model must NOT report a resolve rate.
    (tmp_path / "singleshot" / "ungraded").mkdir()
    (tmp_path / "singleshot" / "ungraded" / "results.json").write_text(json.dumps({
        "model": "vendor/ungraded", "n": 2, "total_cost_usd": 0.0, "rows": []}))
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "run1.jsonl").write_text("\n".join([
        json.dumps({"ts": 1.0, "run_id": "run1", "instance_id": "r__r-1",
                    "stage": "retrieval", "event": "started", "attempt": 0, "detail": {}}),
        json.dumps({"ts": 9.0, "run_id": "run1", "instance_id": "r__r-1",
                    "stage": "loop", "event": "succeeded", "attempt": 1,
                    "detail": {"green": True}}),
    ]))
    monkeypatch.setattr(srv, "SINGLESHOT", tmp_path / "singleshot")
    monkeypatch.setattr(srv, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(srv, "CALIBRATION", tmp_path / "calibration")
    return TestClient(srv.app)


def test_results_discovers_models_from_disk(client):
    models = client.get("/api/results").json()["models"]
    assert {m["model"] for m in models} == {"vendor/modelx", "vendor/ungraded"}


def test_resolve_rate_is_null_until_something_was_graded(client):
    """An ungraded model showing 0.0% resolved would be a lie."""
    models = {m["model"]: m for m in client.get("/api/results").json()["models"]}
    assert models["vendor/modelx"]["resolve_rate"] == pytest.approx(0.25)
    assert models["vendor/ungraded"]["resolve_rate"] is None


def test_instance_verdicts(client):
    rows = client.get("/api/results/modelx/instances").json()["instances"]
    verdicts = {r["instance_id"]: r["verdict"] for r in rows}
    assert verdicts == {"r__r-1": "resolved", "r__r-2": "empty"}


def test_instance_detail_serves_the_diff(client):
    d = client.get("/api/results/modelx/instances/r__r-1").json()
    assert d["resolved"] is True and d["diff"].startswith("diff --git")


def test_unknown_model_and_instance_404(client):
    assert client.get("/api/results/nope/instances").status_code == 404
    assert client.get("/api/results/modelx/instances/nope").status_code == 404


def test_runs_list_and_events(client):
    runs = client.get("/api/runs").json()["runs"]
    assert len(runs) == 1
    assert runs[0]["green"] is True and runs[0]["live"] is False
    assert runs[0]["duration_s"] == 8.0
    events = client.get("/api/runs/run1").json()["events"]
    assert [e["stage"] for e in events] == ["retrieval", "loop"]


def test_meta_exposes_diary_vocabulary(client):
    meta = client.get("/api/meta").json()
    assert "retrieval" in meta["stages"] and "succeeded" in meta["events"]
