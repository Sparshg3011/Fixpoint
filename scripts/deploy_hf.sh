#!/usr/bin/env bash
# Deploy to a Hugging Face Docker Space. Docker Spaces are a PRO feature
# ($9/month) — the free route is Render, see docs/DEPLOY.md.
#
#   scripts/deploy_hf.sh <hf-username>/<space-name>
#
# The Space repository is treated as a BUILD ARTIFACT, not a mirror of the
# source: it receives only what the image needs plus the README front-matter
# Spaces requires (which would render as an ugly table on GitHub, so it never
# lives in the real README). Each deploy force-pushes one fresh commit.
#
# git will ask for credentials: your HF username and an access token with
# WRITE scope (https://huggingface.co/settings/tokens) as the password.
set -euo pipefail

SPACE="${1:?usage: scripts/deploy_hf.sh <hf-username>/<space-name>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

[ -f "$ROOT/deploy/snapshot/MANIFEST.json" ] || {
  echo "no snapshot — run: python scripts/make_snapshot.py" >&2; exit 1; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

cp -R "$ROOT/Dockerfile" "$ROOT/.dockerignore" "$ROOT/fixpoint" "$ROOT/web" "$ROOT/deploy" "$STAGE/"
find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} +

cat > "$STAGE/README.md" <<'FRONTMATTER'
---
title: Fixpoint
emoji: 🔧
colorFrom: green
colorTo: blue
sdk: docker
app_port: 8080
pinned: false
license: mit
short_description: An agent that fixes real GitHub issues — benchmarked at $0
---

Source, benchmark results and design notes: https://github.com/Sparshg3011/Fixpoint
FRONTMATTER

cd "$STAGE"
git init -q -b main
git add -A
git commit -q -m "Deploy $(git -C "$ROOT" rev-parse --short HEAD)"
git push --force "https://huggingface.co/spaces/$SPACE" main

echo
echo "pushed. Build logs: https://huggingface.co/spaces/$SPACE"
echo "live at:            https://$(echo "$SPACE" | tr '/' '-' | tr '[:upper:]' '[:lower:]').hf.space"
