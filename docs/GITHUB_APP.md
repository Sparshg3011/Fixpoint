# Running the PR flow as a GitHub App

By default Fixpoint opens pull requests as whoever is logged into the `gh`
CLI — fine on a laptop, wrong for a server. A hosted Fixpoint should act as a
**GitHub App**: PRs come from `fixpoint[bot]` with the bot badge, the App holds
only the permissions you grant, and it authenticates with one-hour tokens
instead of anyone's personal login.

## One-time setup (about five minutes)

1. **Register the App** at *GitHub → Settings → Developer settings → GitHub Apps
   → New GitHub App*.
   - Name: `Fixpoint` (the slug becomes the bot's login, `fixpoint[bot]`)
   - Homepage URL: your deployment or the repo
   - Webhook: **uncheck "Active"** — Fixpoint doesn't need webhooks
   - Repository permissions: **Contents: Read and write**, **Pull requests:
     Read and write**, Metadata: Read-only (added automatically)
   - Where can this App be installed: *Only on this account* is the safe
     default
2. **Generate a private key** on the App's settings page. A `.pem` file
   downloads — keep it out of the repo (the `.gitignore` already excludes
   `*.pem`).
3. **Install the App** (*Install App* in the left sidebar) on your account, and
   choose the repositories it may touch. This list *is* the safety boundary:
   Fixpoint refuses to open a PR on any repository the App isn't installed on.
4. **Configure Fixpoint** in `.env`:

   ```
   FIXPOINT_GH_APP_ID=123456
   FIXPOINT_GH_APP_KEY=/absolute/path/to/fixpoint.2026-08-30.private-key.pem
   # optional — only needed if the App has more than one installation
   FIXPOINT_GH_INSTALLATION_ID=98765432
   ```

That's it. When those variables are present the PR flow switches to App mode
automatically; remove them and it falls back to the `gh` login.

## What changes in App mode

| | personal mode (`gh`) | App mode |
|:---|:---|:---|
| PR author | your account | `fixpoint[bot]` |
| target | your fork of the project | any repo the App is installed on |
| safety rule | owner must be you | App must be installed there |
| credentials | your `gh` login | RS256 app JWT → 1-hour installation token |
| commits | `fixpoint-agent` | attributed to the bot account |

The branch layout is identical in both modes: `fixpoint/base-<run>` at the
exact commit the agent worked from, `fixpoint/fix-<run>` one commit ahead of
it, and a PR from fix into base so the reviewable diff is exactly the agent's
patch. Dry runs still take zero outward actions.

## Fixing third-party projects

The App can only write where it is installed, so for a project you don't own
the pattern is: fork it, install the App on your fork, point Fixpoint at the
fork. The PR lands in your fork for review; submitting it upstream stays a
human decision. This is deliberate — a bot that files PRs into strangers'
repositories is how automation gets banned.
