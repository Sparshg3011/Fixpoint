"""GitHub App mode: bot identity, installation-scoped safety, header-carried tokens.

GitHub is faked at the HTTP layer (httpx.MockTransport) so the exact API
contract is exercised without a network, and the App private key is generated
in-test so no real credential ever touches the suite.
"""

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from fixpoint import github_app as ga
from fixpoint import pr as pr_mod

TOKEN = "ghs_SECRET_installation_token"


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return pem, pub


def fake_github(calls):
    """Just enough of api.github.com for one installation on one repo."""
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        calls.append((request.method, p))
        if p == "/app":
            return httpx.Response(200, json={"slug": "fixpoint"})
        if p == "/app/installations":
            return httpx.Response(200, json=[{"id": 42}])
        if p == "/app/installations/42/access_tokens":
            return httpx.Response(201, json={"token": TOKEN})
        if p.startswith("/users/"):
            return httpx.Response(200, json={"id": 777})
        if p == "/installation/repositories":
            return httpx.Response(200, json={"repositories": [{"full_name": "Sparshg3011/demo"}]})
        if p == "/repos/Sparshg3011/demo/pulls" and request.method == "POST":
            return httpx.Response(201, json={"html_url": "https://github.com/Sparshg3011/demo/pull/1"})
        return httpx.Response(404, json={"message": "not found"})
    return httpx.MockTransport(handler)


def make_client(keypair, calls):
    return ga.AppClient("123", keypair[0], transport=fake_github(calls))


def _quiet_git(monkeypatch, tmp_path, git_calls):
    """Fake out every process the PR flow would spawn; record git invocations."""
    monkeypatch.setattr(pr_mod, "_git", lambda *args, cwd=None: git_calls.append(args) or "")
    monkeypatch.setattr(pr_mod, "bare_path", lambda repo: tmp_path)
    monkeypatch.setattr(pr_mod.subprocess, "run",
                        lambda *a, **k: type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})())


def test_app_jwt_is_rs256_signed_and_within_githubs_ten_minute_cap(keypair):
    pem, pub = keypair
    token = ga.app_jwt("123", pem, now=1_000_000)
    claims = jwt.decode(token, pub, algorithms=["RS256"], options={"verify_exp": False})
    assert claims["iss"] == "123"
    assert claims["exp"] - claims["iat"] <= 600


def test_safety_rule_is_the_installation_list(keypair):
    c = make_client(keypair, [])
    c.assert_installed("sparshg3011/demo")  # installed; case-insensitive
    with pytest.raises(ga.AppNotInstalledError, match="not installed"):
        c.assert_installed("django/django")


def test_bot_identity_carries_the_apps_avatar(keypair):
    c = make_client(keypair, [])
    assert c.bot_login() == "fixpoint[bot]"
    assert c.commit_identity() == ("fixpoint[bot]", "777+fixpoint[bot]@users.noreply.github.com")


def test_token_travels_in_a_header_never_a_url(keypair):
    cfg = make_client(keypair, []).push_config()
    assert cfg[0] == "-c"
    assert cfg[1].startswith("http.https://github.com/.extraheader=AUTHORIZATION: bearer ")


def test_app_mode_dry_run_takes_no_outward_action(keypair, monkeypatch, tmp_path):
    calls, git_calls = [], []
    monkeypatch.setattr(ga, "client_if_configured", lambda: make_client(keypair, calls))
    _quiet_git(monkeypatch, tmp_path, git_calls)
    res = pr_mod.open_pr(upstream="Sparshg3011/demo", base_commit="abc123", patch="diff",
                         instance_id="run-1", dry_run=True)
    assert res.dry_run is True and res.actor == "fixpoint[bot]"
    assert "as fixpoint[bot]" in res.url
    assert not any("push" in a for a in git_calls)
    assert not any(m == "POST" and p.endswith("/pulls") for m, p in calls)


def test_app_mode_execute_pushes_with_header_auth_and_opens_the_pr(keypair, monkeypatch, tmp_path):
    calls, git_calls = [], []
    monkeypatch.setattr(ga, "client_if_configured", lambda: make_client(keypair, calls))
    _quiet_git(monkeypatch, tmp_path, git_calls)
    res = pr_mod.open_pr(upstream="Sparshg3011/demo", base_commit="abc123", patch="diff",
                         instance_id="run-1", dry_run=False)
    assert res.url == "https://github.com/Sparshg3011/demo/pull/1"
    assert res.actor == "fixpoint[bot]"
    pushes = [a for a in git_calls if "push" in a]
    assert len(pushes) == 2  # base branch, then fix branch
    for a in pushes:
        assert a[0] == "-c" and "AUTHORIZATION: bearer" in a[1]  # header auth
        assert all(TOKEN not in x for x in a if x.startswith("https://"))  # clean URLs
    assert ("POST", "/repos/Sparshg3011/demo/pulls") in calls
    # commits are attributed to the bot, not to a placeholder author
    assert any("user.name=fixpoint[bot]" in a for a in git_calls)


def test_uninstalled_target_is_a_pr_safety_error(keypair, monkeypatch, tmp_path):
    monkeypatch.setattr(ga, "client_if_configured", lambda: make_client(keypair, []))
    _quiet_git(monkeypatch, tmp_path, [])
    with pytest.raises(pr_mod.PRSafetyError, match="not installed"):
        pr_mod.open_pr(upstream="django/django", base_commit="abc", patch="d",
                       instance_id="x", dry_run=True)


def test_personal_mode_is_the_default_when_no_app_is_configured(monkeypatch):
    for k in ("FIXPOINT_GH_APP_ID", "FIXPOINT_GH_APP_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert ga.client_if_configured() is None
