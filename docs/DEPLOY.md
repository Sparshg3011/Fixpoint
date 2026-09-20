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
(`FIXPOINT_CACHE_MAX_MB`).

Memory is not the constraint it first looked like. Under a hard 512 MB limit
with no swap, a django-sized run (1,859 indexed files) peaks at 137 MiB of
process memory and never touches the limit; the 484 MiB that `docker stats`
once showed was page cache from the clone, which the kernel hands back.

CPU is the constraint, and most of it was self-inflicted. The image sets
`FIXPOINT_SHALLOW_CLONES=1`, which fetches the single commit a run asked about
instead of the repository's whole history. Measured on django at a tenth of a
CPU, the worst case a free instance can be given:

| | full mirror | single commit |
|:---|---:|---:|
| checkout + index | 540 s | 50 s |
| first model call at | 662 s | 152 s |
| mirror on disk | 283 MB | 12 MB |

Benchmark campaigns keep the full mirror — they revisit dozens of commits per
repository and would pay the fetch over and over.

End to end on that same tenth of a CPU, django takes about twelve minutes and
peaks at 89 MiB; a small repository like `pallets/click` answers in under
three. Most of that is the model thinking, not the host.

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

## Option A — Render (free, no card)

A free Render web service builds the Dockerfile straight from GitHub. What the
free instance is, as of September 2026: 512 MB of RAM, asleep after 15 minutes
without a request (the next visitor waits about a minute while it wakes), and
no persistent disk — runs made on the host are gone after a sleep or redeploy,
while the recorded replays are re-seeded from the image at every boot.

1. Sign in at render.com with GitHub. No payment method is asked for.
2. Open [render.com/deploy?repo=…/Fixpoint](https://render.com/deploy?repo=https://github.com/Sparshg3011/Fixpoint)
   (for a fork, swap in its URL; *New → Blueprint* in the dashboard is the same
   thing). Render reads `render.yaml` and asks for one value, `NVIDIA_API_KEY`.
   Paste it, *Apply*, and wait for the build.
3. Open the service's *Environment* tab and copy `FIXPOINT_ACCESS_TOKEN` —
   Render generated it. That is what the site asks for before a fix run.

Every push to `main` redeploys. To publish PRs from the host, add the GitHub
App trio in the same tab (`FIXPOINT_GH_APP_ID`, `FIXPOINT_GH_INSTALLATION_ID`,
`FIXPOINT_GH_APP_KEY_B64` = `base64 < your-key.pem`) — see [GITHUB_APP.md](GITHUB_APP.md).

`render.yaml` lowers `FIXPOINT_MAX_REPO_MB` to 120: the free instance's sliver
of CPU makes bigger clones a test of patience, not of memory. Raise it if you
don't mind the wait — the live view keeps the instance awake while you watch.

## Option B — Google Cloud Run (free grant, ~10x the CPU, needs a card)

Cloud Run's always-free grant (180,000 vCPU-seconds and 360,000 GiB-seconds a
month) covers roughly a hundred runs, and gives a **full vCPU while a request
is in flight** against Render's tenth of one. The catch is a billing account,
so a card, even though this workload stays inside the grant. Set a spend cap —
Cloud Run is one of the services they cover.

```bash
deploy/cloudrun.sh <your-gcp-project-id>
```

The script exists because four Cloud Run defaults break this app in ways that
look like Fixpoint bugs: a 300-second request timeout cuts the live view off
mid-run, autoscaling lets one instance stream a run another instance is doing,
CPU is throttled to nearly zero between requests (so a background run freezes
when nobody watches), and the "disk" is really RAM. It sets `--timeout=3600`,
`--max-instances=1`, `--no-cpu-throttling` and `--memory=1Gi` accordingly, then
prints the two commands that add your secrets without putting them in history.

## Option C — Fly.io (a few dollars a month, persistent volume)

Scale-to-zero VM with 512 MB of RAM and a 3 GB volume, so runs made on the host
survive restarts and repo mirrors stay cached. Needs a card on file. Note that
`shared-cpu-1x` is billed a *baseline* slice of a core, so on CPU alone this is
not an upgrade over Render's free instance — the volume is the reason to pick
it.

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

## Option D — Hugging Face Spaces (needs PRO)

Docker Spaces used to be free; they are now part of PRO ($9/month). With PRO
the hardware is generous (2 vCPU / 16 GB) and the flow is: create a *Docker →
Blank* Space, add the same secrets under *Settings → Variables and secrets*,
then `scripts/deploy_hf.sh <hf-username>/<space-name>` (git asks for your HF
username and a **write** token). The Space repo is a build artifact: the script
pushes only what the image needs plus the README front-matter Spaces requires.

## Operating notes

- **Restarts are safe.** Fix runs are threads in the server process; whatever a
  restart interrupts is given a terminal "server restarted" event at the next
  boot, so nothing shows as live forever.
- **The live view survives proxies.** The event stream numbers its events,
  sends keepalives through the model's long silences, and resumes after
  `Last-Event-ID` — a dropped connection reconnects without gaps or duplicates.
- **Free-tier models are mortal.** If the configured model is retired, set
  `FIXPOINT_MODEL` to a living one from the provider's catalog and restart.
