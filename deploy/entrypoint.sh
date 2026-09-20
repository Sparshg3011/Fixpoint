#!/bin/sh
# Boot sequence for the hosted image: prepare the state dir, seed the recorded
# runs, drop root, start the server.
set -e

STATE="${FIXPOINT_STATE_DIR:-/app}"
mkdir -p "$STATE/runs" "$STATE/data/repos"

# Seed replays without clobbering: a redeploy adds new recordings, and runs
# made on this host (they live on the volume) are never overwritten.
for f in /app/snapshot/replays/*; do
  if [ -e "$f" ] && [ ! -e "$STATE/runs/$(basename "$f")" ]; then
    cp "$f" "$STATE/runs/"
  fi
done

if [ "$(id -u)" = "0" ]; then
  # A freshly mounted volume belongs to root. Fix ownership once (not on every
  # boot — the mirror cache can hold a lot of files), then never run as root.
  [ "$(stat -c %U "$STATE/runs")" = "app" ] || chown -R app:app "$STATE/runs" "$STATE/data"
  [ "$(stat -c %U "$STATE")" = "app" ] || chown app:app "$STATE" 2>/dev/null || true
  exec setpriv --reuid=app --regid=app --init-groups python -m fixpoint.server
fi
exec python -m fixpoint.server
