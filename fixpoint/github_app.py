"""GitHub App identity for the PR flow — bot-authored PRs, no personal login.

A hosted Fixpoint must never carry a person's GitHub credentials. A GitHub App
is the identity built for this: it is installed on specific repositories, acts
as `<slug>[bot]` with the bot badge, holds only the permissions granted
(Contents + Pull requests), and authenticates with tokens that expire in an
hour instead of a long-lived personal token.

Auth flow (GitHub's, not ours):
  private key  ->  RS256 app JWT (<=10 min)  ->  installation access token
                                                 (1 hour, scoped to the repos
                                                 the App is installed on)

Safety rule in App mode: the target repository must be one the App is
INSTALLED on. That is the bot-world equivalent of "you own it" — an owner has
to grant access deliberately — so PRs against arbitrary upstreams remain
impossible by construction, exactly as in the personal-fork mode.

Configuration (in .env, never in commands or logs):
  FIXPOINT_GH_APP_ID            numeric App ID
  FIXPOINT_GH_APP_KEY           path to the downloaded private key (.pem)
  FIXPOINT_GH_INSTALLATION_ID   optional; discovered when the App has exactly
                                one installation
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

API = "https://api.github.com"
_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


class AppNotInstalledError(Exception):
    """The App is not installed on the target repository."""


def configured() -> bool:
    return bool(os.environ.get("FIXPOINT_GH_APP_ID") and os.environ.get("FIXPOINT_GH_APP_KEY"))


def app_jwt(app_id: str, private_key_pem: str, now: float | None = None) -> str:
    """The App's self-signed identity token. GitHub caps exp at 10 minutes;
    iat is backdated a minute to absorb clock skew between us and GitHub."""
    import jwt  # local import: only the App path pays for the dependency

    t = int(now if now is not None else time.time())
    return jwt.encode({"iat": t - 60, "exp": t + 540, "iss": app_id},
                      private_key_pem, algorithm="RS256")


class AppClient:
    """One App installation, lazily authenticated. `transport` exists so tests
    can drive the exact HTTP contract without a network."""

    def __init__(self, app_id: str, private_key_pem: str, installation_id: str | None = None,
                 transport: httpx.BaseTransport | None = None):
        self.app_id = app_id
        self._pem = private_key_pem
        self._installation_id = installation_id
        self._http = httpx.Client(base_url=API, headers=_HEADERS, timeout=30, transport=transport)
        self._token: str | None = None
        self._token_expires = 0.0
        self._slug: str | None = None

    @classmethod
    def from_env(cls) -> AppClient:
        return cls(os.environ["FIXPOINT_GH_APP_ID"],
                  Path(os.environ["FIXPOINT_GH_APP_KEY"]).read_text(),
                  os.environ.get("FIXPOINT_GH_INSTALLATION_ID") or None)

    # -- identity -----------------------------------------------------------

    def _as_app(self) -> dict:
        return {"Authorization": f"Bearer {app_jwt(self.app_id, self._pem)}"}

    def slug(self) -> str:
        if self._slug is None:
            r = self._http.get("/app", headers=self._as_app())
            r.raise_for_status()
            self._slug = r.json()["slug"]
        return self._slug

    def bot_login(self) -> str:
        return f"{self.slug()}[bot]"

    def commit_identity(self) -> tuple[str, str]:
        """Author name/email that GitHub attributes to the bot account, so the
        commits carry the App's avatar rather than an anonymous author."""
        login = self.bot_login()
        r = self._http.get(f"/users/{login}", headers=self._as_app())
        uid = r.json().get("id") if r.status_code == 200 else None
        email = f"{uid}+{login}@users.noreply.github.com" if uid else f"{login}@users.noreply.github.com"
        return login, email

    def installation_id(self) -> str:
        if self._installation_id is None:
            r = self._http.get("/app/installations", headers=self._as_app())
            r.raise_for_status()
            ids = [str(i["id"]) for i in r.json()]
            if len(ids) != 1:
                raise RuntimeError(
                    f"App has {len(ids)} installations; set FIXPOINT_GH_INSTALLATION_ID "
                    "to choose one")
            self._installation_id = ids[0]
        return self._installation_id

    def token(self) -> str:
        """Installation access token, refreshed a minute before expiry."""
        if self._token is None or time.time() > self._token_expires - 60:
            r = self._http.post(f"/app/installations/{self.installation_id()}/access_tokens",
                                headers=self._as_app())
            r.raise_for_status()
            self._token = r.json()["token"]
            self._token_expires = time.time() + 3600
        return self._token

    def _as_installation(self) -> dict:
        return {"Authorization": f"Bearer {self.token()}"}

    # -- the safety rule ----------------------------------------------------

    def accessible_repos(self) -> set[str]:
        repos: set[str] = set()
        page = 1
        while True:
            r = self._http.get("/installation/repositories",
                               params={"per_page": 100, "page": page},
                               headers=self._as_installation())
            r.raise_for_status()
            batch = r.json().get("repositories", [])
            repos.update(x["full_name"].lower() for x in batch)
            if len(batch) < 100:
                return repos
            page += 1

    def assert_installed(self, repo: str) -> None:
        if repo.lower() not in self.accessible_repos():
            raise AppNotInstalledError(
                f"refusing to open a PR against {repo!r}: the {self.bot_login()} App is not "
                "installed there. Install it on that repository (or your fork of it) first.")

    # -- publishing ---------------------------------------------------------

    def push_config(self) -> list[str]:
        """`git -c` arguments that authenticate a push over HTTPS. The token
        rides in a header, so it never appears in a remote URL — and therefore
        never in git's error messages or any log that quotes them."""
        return ["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: bearer {self.token()}"]

    def create_pr(self, repo: str, *, base: str, head: str, title: str, body: str) -> str:
        r = self._http.post(f"/repos/{repo}/pulls", headers=self._as_installation(),
                            json={"title": title, "head": head, "base": base, "body": body})
        r.raise_for_status()
        return r.json()["html_url"]


def client_if_configured() -> AppClient | None:
    """The App client when credentials are present; None means personal mode."""
    return AppClient.from_env() if configured() else None
