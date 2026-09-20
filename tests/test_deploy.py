"""What a public deployment must guarantee — asserted, not hoped for.

Every test here exists because of a concrete way a hosted Fixpoint could go
wrong: an unsecured instance relaying someone's model quota, a proxy freezing
the live view, a restart leaving phantom runs, a private repository leaking
into a public snapshot, a git argv built from internet input.
"""

import base64
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import fixpoint.diary as diary_mod
import fixpoint.server as srv
from fixpoint import github_app, service

ROOT = Path(__file__).resolve().parent.parent


def _request(client_host="127.0.0.1", headers=()):
    return Request({"type": "http", "client": (client_host, 1234),
                    "headers": [(k.encode(), v.encode()) for k, v in headers]})


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "RUNS", tmp_path)
    monkeypatch.delenv("FIXPOINT_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.setattr(service, "start_fix", lambda *a, **k: "run-1")
    return TestClient(srv.app)


# --- the gate: fail closed ---------------------------------------------------

def test_unsecured_remote_deployment_is_read_only(client):
    """No token configured + a caller that is not this machine = no mutation.
    Forgetting a secret must yield a safe showcase, never an open relay."""
    r = client.post("/api/fix", json={"repo": "a/b", "issue_text": "x"})
    assert r.status_code == 403
    assert client.get("/api/meta").json()["fix"] == {"enabled": False, "needs_token": False}
    assert client.get("/api/results").status_code == 200  # reading stays public


def test_token_is_required_once_configured(client, monkeypatch):
    monkeypatch.setenv("FIXPOINT_ACCESS_TOKEN", "s3cret")
    body = {"repo": "a/b", "issue_text": "x"}
    assert client.post("/api/fix", json=body).status_code == 401
    assert client.post("/api/fix", json=body, headers={"X-Fixpoint-Token": "nope"}).status_code == 401
    ok = client.post("/api/fix", json=body, headers={"X-Fixpoint-Token": "s3cret"})
    assert ok.status_code == 200 and ok.json() == {"run_id": "run-1"}
    assert client.get("/api/meta").json()["fix"] == {"enabled": True, "needs_token": True}
    # the PR endpoint sits behind the same gate
    assert client.post("/api/fix/whatever/pr", json={}).status_code == 401


def test_local_means_loopback_and_untouched_by_a_proxy(monkeypatch):
    monkeypatch.delenv("HOST", raising=False)
    assert srv._is_local(_request("127.0.0.1")) is True
    assert srv._is_local(_request("10.0.0.7")) is False
    assert srv._is_local(_request("127.0.0.1", [("x-forwarded-for", "1.2.3.4")])) is False
    # told to listen on every interface = hosted, whatever the peer looks like
    monkeypatch.setenv("HOST", "0.0.0.0")
    assert srv._is_local(_request("127.0.0.1")) is False


def test_busy_maps_to_429(client, monkeypatch):
    monkeypatch.setenv("FIXPOINT_ACCESS_TOKEN", "t")

    def busy(*a, **k):
        raise service.Busy("1 fix run(s) already in progress")
    monkeypatch.setattr(service, "start_fix", busy)
    r = client.post("/api/fix", json={"repo": "a/b"}, headers={"X-Fixpoint-Token": "t"})
    assert r.status_code == 429


# --- the live stream survives proxies ------------------------------------------

def _write_diary(path: Path, stages):
    path.write_text("".join(json.dumps({
        "ts": float(i), "run_id": path.stem, "instance_id": "r/r", "stage": st,
        "event": "succeeded", "attempt": 0, "detail": {}}) + "\n" for i, st in enumerate(stages)))


def test_stream_numbers_events_and_resumes_after_last_event_id(client, tmp_path):
    _write_diary(tmp_path / "run9.jsonl", ["sandbox", "retrieval", "loop"])
    full = client.get("/api/runs/run9/stream").text
    assert [ln for ln in full.splitlines() if ln.startswith("id:")] == ["id: 0", "id: 1", "id: 2"]
    # a reconnecting EventSource sends Last-Event-ID; nothing may be replayed twice
    resumed = client.get("/api/runs/run9/stream", headers={"Last-Event-ID": "1"}).text
    assert [ln for ln in resumed.splitlines() if ln.startswith("id:")] == ["id: 2"]


# --- internet input never becomes a git option ----------------------------------

@pytest.mark.parametrize("ref", ["--upload-pack=touch /tmp/x", "-x", "main branch", "a" * 300])
def test_option_shaped_refs_are_refused_before_git_runs(ref, monkeypatch):
    monkeypatch.setattr(service.subprocess, "run",
                        lambda *a, **k: pytest.fail("git must not run for a bad ref"))
    with pytest.raises(ValueError, match="not a valid"):
        service.resolve_ref("a/b", ref)


def test_unreadable_repo_gets_a_human_answer(monkeypatch):
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **k: type(
        "P", (), {"returncode": 128, "stdout": "", "stderr": "could not read Username"})())
    with pytest.raises(ValueError, match="PUBLIC repositories"):
        service.resolve_ref("someone/private-repo", None)


# --- capacity ---------------------------------------------------------------------

@pytest.fixture
def quiet_service(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(diary_mod, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(service, "load_env", lambda: True)
    monkeypatch.setattr(service, "resolve_ref", lambda repo, ref: "abc1234")
    monkeypatch.setattr(service, "check_repo_size", lambda repo: None)
    monkeypatch.setattr(service, "_active", 0)
    return tmp_path


def test_slots_are_capped_and_always_released(quiet_service, monkeypatch):
    import threading

    monkeypatch.setenv("FIXPOINT_MAX_CONCURRENT", "1")
    release = threading.Event()

    def slow_fix(*a, **k):
        release.wait(5)
        raise RuntimeError("boom")  # even a crashing run must give its slot back
    monkeypatch.setattr(service, "fix_issue", slow_fix)

    service.start_fix("a/b", "issue", None)
    with pytest.raises(service.Busy):
        service.start_fix("a/c", "issue", None)
    release.set()
    for t in threading.enumerate():
        if t.name.startswith("fix-"):
            t.join(5)
    assert service._active == 0


def test_repo_size_cap_refuses_big_and_fails_open_on_api_trouble(monkeypatch):
    import httpx

    monkeypatch.setenv("FIXPOINT_MAX_REPO_MB", "100")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: type(
        "R", (), {"status_code": 200, "json": lambda self: {"size": 500 * 1024}})())
    with pytest.raises(ValueError, match="caps repositories"):
        service.check_repo_size("big/repo")

    def down(*a, **k):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(httpx, "get", down)
    service.check_repo_size("any/repo")  # a seatbelt, not a gate: must not raise


# --- restarts ------------------------------------------------------------------------

def test_orphaned_runs_get_an_ending(quiet_service):
    _write_diary(quiet_service / "fix-a-b-1.jsonl", ["sandbox", "retrieval"])     # lost its worker
    _write_diary(quiet_service / "fix-a-b-2.jsonl", ["sandbox", "loop"])          # finished
    assert service.close_orphaned_runs() == 1
    last = diary_mod.read(quiet_service / "fix-a-b-1.jsonl")[-1]
    assert (last.stage, last.event) == ("loop", "failed")
    assert "restarted" in last.detail["error"]
    assert service.close_orphaned_runs() == 0  # idempotent


# --- secrets arrive as text on a host ---------------------------------------------------

def test_app_key_accepts_a_path_pem_text_or_base64(tmp_path, monkeypatch):
    pem = "-----BEGIN PRIVATE KEY-----\nabc\ndef\n-----END PRIVATE KEY-----\n"
    monkeypatch.delenv("FIXPOINT_GH_APP_KEY_B64", raising=False)

    (tmp_path / "k.pem").write_text(pem)
    monkeypatch.setenv("FIXPOINT_GH_APP_KEY", str(tmp_path / "k.pem"))
    assert github_app.private_key_from_env() == pem

    monkeypatch.setenv("FIXPOINT_GH_APP_KEY", pem.replace("\n", "\\n"))  # flattened by a dashboard
    assert github_app.private_key_from_env() == pem

    monkeypatch.delenv("FIXPOINT_GH_APP_KEY")
    monkeypatch.setenv("FIXPOINT_GH_APP_KEY_B64", base64.b64encode(pem.encode()).decode())
    monkeypatch.setenv("FIXPOINT_GH_APP_ID", "1")
    assert github_app.configured() and github_app.private_key_from_env() == pem


# --- what ships -----------------------------------------------------------------------------

def _snapshot_module():
    spec = importlib.util.spec_from_file_location("make_snapshot", ROOT / "scripts" / "make_snapshot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fix_runs_ship_only_when_github_confirms_the_repo_is_public(tmp_path, monkeypatch):
    """The operator's keychain lets local runs read PRIVATE repos; their diaries
    hold file names and diffs. Unconfirmed = withheld."""
    snap = _snapshot_module()
    diary = tmp_path / "fix-me-secret-1.jsonl"
    diary.write_text("{}\n")
    diary.with_suffix(".meta.json").write_text(json.dumps({"repo": "me/secret"}))

    monkeypatch.setattr(snap, "_is_public_repo", lambda repo: False)
    assert snap._fix_run_is_shippable(diary)[0] is False
    monkeypatch.setattr(snap, "_is_public_repo", lambda repo: True)
    assert snap._fix_run_is_shippable(diary) == (True, "me/secret")
    diary.with_suffix(".meta.json").unlink()  # no sidecar -> cannot know -> withheld
    assert snap._fix_run_is_shippable(diary)[0] is False


def test_the_hosted_surface_never_imports_the_benchmark_stack():
    """deploy/requirements.txt installs four packages. If the server's import
    path ever reaches datasets/pandas/swebench again, the image breaks at boot —
    so the edge is asserted here, in a clean interpreter."""
    code = ("import sys, fixpoint.server, fixpoint.service, fixpoint.pr, fixpoint.github_app;"
            "bad = {'datasets','pandas','pyarrow','numpy','swebench','anthropic','docker'} & set(sys.modules);"
            "sys.exit(f'heavy imports on the hosted path: {sorted(bad)}' if bad else 0)")
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_the_render_blueprint_matches_the_app_and_carries_no_secret():
    """render.yaml is what a one-click deploy executes. It must point the
    health check at a route that exists, stay on the free plan it documents,
    and never hold a credential's value — those are typed into the dashboard
    or generated by the host."""
    yaml = pytest.importorskip("yaml")
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text())
    (svc,) = blueprint["services"]
    assert (svc["type"], svc["runtime"], svc["plan"]) == ("web", "docker", "free")
    assert svc["healthCheckPath"] in {r.path for r in srv.app.routes}

    env = {e["key"]: e for e in svc["envVars"]}
    assert env["NVIDIA_API_KEY"] == {"key": "NVIDIA_API_KEY", "sync": False}
    assert env["FIXPOINT_ACCESS_TOKEN"].get("generateValue") is True
    for key, entry in env.items():
        if any(word in key for word in ("KEY", "TOKEN", "SECRET")):
            assert "value" not in entry, f"{key} must not carry a literal value"


def test_the_cloud_run_recipe_sets_the_four_defaults_that_would_break_a_run():
    """Cloud Run's defaults are wrong for this app in ways that surface as
    Fixpoint bugs: runs cut off at 300s, a stream routed to an instance that
    isn't running the job, a background thread frozen between requests, and a
    checkout that counts against RAM because the filesystem is memory."""
    script = (ROOT / "deploy" / "cloudrun.sh").read_text()
    for flag in ("--timeout 3600", "--max-instances 1",
                 "--no-cpu-throttling", "--memory 1Gi"):
        assert flag in script, f"cloudrun.sh must pass {flag}"
    assert "NVIDIA_API_KEY=" not in script.replace('NVIDIA_API_KEY="\\$KEY"', "")


def test_the_image_fetches_single_commits_not_whole_histories():
    """The hosted flow reads one commit. Cloning a decade of history instead
    cost 11 minutes on a free instance — and on hosts whose filesystem is RAM,
    it costs memory too."""
    assert "FIXPOINT_SHALLOW_CLONES=1" in (ROOT / "Dockerfile").read_text()


def test_the_image_gives_the_model_room_to_finish_a_large_repo():
    """Measured on django (5 files, 116k tokens of input): at the library
    default of 8000 the model is cut off mid-reasoning and emits no edits at
    all, even after the doubling retry. At 16000 it lands the patch."""
    assert "FIXPOINT_MAX_TOKENS=16000" in (ROOT / "Dockerfile").read_text()
