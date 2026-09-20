# Deploying Fixpoint

The hosted app is one small container: the scoreboard, the instance explorer,
the run replays, and — for whoever holds the access token — live fix runs.
Benchmark campaigns are *not* part of it: they need Docker-in-Docker and the
SWE-bench harness and stay a local tool. The hosted flow verifies patches
statically (`git apply --check`), so the image is just Python, git, and four
packages (`deploy/requirements.txt`).

## What ships

`data/` and `runs/` are gitignored (they sit next to gigabytes of mirrors and
logs), so the site's content comes from a curated snapshot:

```bash
python scripts/make_snapshot.py     # rebuilds deploy/snapshot/ (~5 MB)
```

It copies exactly what the local UI shows — same discovery rule as the server —
minus raw model responses. Product-flow runs (`fix-*`) are included **only if
GitHub's unauthenticated API confirms the repository is public**: locally, git
can read your private repos through the keychain, and a diary holds file names
and a diff. Anything unconfirmed is withheld. Re-run the script and redeploy
whenever you want the public site to catch up with local results.

## Who can do what

Reading is public. Starting a fix run or opening a PR spends the operator's
model quota and clones repositories onto the host, so both sit behind one gate
that fails **closed**:

| deployment state | fix runs / PRs |
|:---|:---|
| `FIXPOINT_ACCESS_TOKEN` set | allowed with the token (sent as `X-Fixpoint-Token`; the UI asks for it) |
| no token, request from this machine, server bound to loopback | allowed — the laptop workflow |
| no token, anything else | refused (403) — the site is a read-only showcase |

So a deployment where you forgot every secret is still safe. Capacity is
bounded too: `FIXPOINT_MAX_CONCURRENT` (1 in the image) returns 429 beyond it,
`FIXPOINT_MAX_REPO_MB` (400) refuses oversized repositories, extracted trees
are dropped after each run, and the mirror cache is LRU-capped
(`FIXPOINT_CACHE_MAX_MB`). Peak memory on a django-sized repository was
measured at 484 MiB — hence the 1 GB VM below; 512 MB is not enough.

Only **public** repositories can be fixed from a host (there is no keychain
there). PRs from a host are opened by the GitHub App — see
[GITHUB_APP.md](GITHUB_APP.md); without it the PR button explains what is missing.

## Try the image locally first

```bash
docker build -t fixpoint .
docker run --rm -p 8080:8080 fixpoint                       # read-only showcase
docker run --rm -p 8080:8080 -e NVIDIA_API_KEY -e FIXPOINT_ACCESS_TOKEN=choose-one fixpoint
```

(`-e NAME` with no value forwards the variable from your shell; `set -a; . ./.env; set +a`
loads `.env` into the shell without echoing anything.)

## Option A — Hugging Face Spaces (free, no card)

2 vCPU / 16 GB on the free tier, which is far more than this needs. Trade-offs:
the Space sleeps after ~48 h without visitors (wakes on the next one), and
there is no persistent disk — runs made on the Space vanish on restart, while
the recorded replays are re-seeded from the image every boot.

1. Create the Space: *huggingface.co → New Space → SDK: **Docker** → Blank*, public.
2. *Settings → Variables and secrets*, add **secrets**: `NVIDIA_API_KEY`,
   `FIXPOINT_ACCESS_TOKEN` (generate one: `python -c "import secrets; print(secrets.token_urlsafe(24))"`),
   and optionally the GitHub App trio (`FIXPOINT_GH_APP_ID`,
   `FIXPOINT_GH_APP_KEY_B64` = `base64 < your-key.pem`, `FIXPOINT_GH_INSTALLATION_ID`).
3. Deploy: `scripts/deploy_hf.sh <hf-username>/<space-name>` — git asks for your
   HF username and a **write** token as the password.

The Space repo is a build artifact: the script pushes only what the image
needs plus the README front-matter Spaces requires.

## Option B — Fly.io (a few dollars a month, persistent volume)

Scale-to-zero VM with a 3 GB volume, so runs made on the host survive
restarts and repo mirrors stay cached. Needs a card on file.

```bash
brew install flyctl && fly auth login
fly launch --copy-config --no-deploy          # adopts fly.toml; pick your app name/region
grep -E '^NVIDIA_API_KEY=' .env | fly secrets import
fly secrets set FIXPOINT_ACCESS_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
fly deploy
```

Note the token somewhere safe before it scrolls away (`fly secrets` never
shows values again). For the GitHub App:
`fly secrets set FIXPOINT_GH_APP_ID=… FIXPOINT_GH_INSTALLATION_ID=… FIXPOINT_GH_APP_KEY_B64="$(base64 < key.pem)"`.

## Operating notes

- **Restarts are safe.** Fix runs are threads in the server process; whatever a
  restart interrupts is given a terminal "server restarted" event at the next
  boot, so nothing shows as live forever.
- **The live view survives proxies.** The event stream numbers its events,
  sends keepalives through the model's long silences, and resumes after
  `Last-Event-ID` — a dropped connection reconnects without gaps or duplicates.
- **Free-tier models are mortal.** If the configured model is retired, set
  `FIXPOINT_MODEL` to a living one from the provider's catalog and restart.
