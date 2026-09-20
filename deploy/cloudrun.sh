#!/usr/bin/env bash
# Deploy the hosted image to Google Cloud Run.
#
#   deploy/cloudrun.sh <gcp-project-id> [region]
#
# Why not just `gcloud run deploy`: four of its defaults are wrong for this
# app, and three of them fail in ways that look like bugs in Fixpoint.
#
#   --timeout=3600        Default is 300s. A fix run streams for 5-25 minutes,
#                         so the default cuts the live view off mid-run.
#   --max-instances=1     A run is a thread with its diary on local disk. Two
#                         instances means the browser can ask instance B to
#                         stream a run that only instance A knows about.
#   --no-cpu-throttling   Outside a request Cloud Run throttles CPU to nearly
#                         zero. The run happens in a background thread, so
#                         without this it freezes whenever nobody is watching.
#   --memory=1Gi          Cloud Run's disk IS memory (tmpfs). A django checkout
#                         is ~87MB of "disk" on top of ~137MiB of process
#                         memory, so 512Mi leaves no headroom.
#
# Secrets are set afterwards, interactively, so no key is ever typed here.
set -euo pipefail

PROJECT="${1:?usage: deploy/cloudrun.sh <gcp-project-id> [region]}"
# us-central1: Cloud Run's always-free grant only applies in the US tier-1
# regions, and this is the one with every feature.
REGION="${2:-us-central1}"
SERVICE="fixpoint"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

command -v gcloud >/dev/null || {
  echo "gcloud not found: https://cloud.google.com/sdk/docs/install" >&2; exit 1; }

gcloud config set project "$PROJECT" >/dev/null
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
       artifactregistry.googleapis.com --quiet

# --source builds with Cloud Build from the Dockerfile; no local docker needed.
gcloud run deploy "$SERVICE" \
  --source "$ROOT" \
  --region "$REGION" \
  --allow-unauthenticated \
  --port 8080 \
  --memory 1Gi \
  --cpu 1 \
  --timeout 3600 \
  --max-instances 1 \
  --min-instances 0 \
  --no-cpu-throttling \
  --set-env-vars FIXPOINT_MAX_REPO_MB=600

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"

cat <<EOF

deployed: $URL

It is read-only until you add the two secrets. Run these — each prompts for the
value, so nothing lands in your shell history:

  read -rs KEY && gcloud run services update $SERVICE --region $REGION \\
      --update-env-vars NVIDIA_API_KEY="\$KEY" && unset KEY

  gcloud run services update $SERVICE --region $REGION \\
      --update-env-vars FIXPOINT_ACCESS_TOKEN="\$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"

Read the token back (it is the password the New fix page asks for):

  gcloud run services describe $SERVICE --region $REGION \\
      --format='value(spec.template.spec.containers[0].env)' | tr ',' '\\n' | grep ACCESS

Then cap spending so a mistake cannot bill you — Cloud Run is covered by
spend caps: https://console.cloud.google.com/billing
EOF
