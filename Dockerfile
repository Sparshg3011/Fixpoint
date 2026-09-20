# Fixpoint — hosted web app (scoreboard, run replays, live fix runs).
#
# This image is the PRODUCT surface only. Benchmark campaigns need Docker-in-
# Docker and the SWE-bench harness and stay a local tool; the hosted flow
# verifies patches statically (git apply --check), so all it needs is Python,
# git, and four small packages.
FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# uid 1000: what Hugging Face Spaces runs containers as, and a sane non-root
# default everywhere else.
RUN useradd --create-home --uid 1000 app

WORKDIR /app
COPY deploy/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY fixpoint/ ./fixpoint/
COPY web/ ./web/
# The curated artifact snapshot (scripts/make_snapshot.py): benchmark results
# are read-only image content; recorded runs seed the state dir on first boot.
COPY deploy/snapshot/artifacts/ ./data/
COPY deploy/snapshot/replays/ ./snapshot/replays/
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && chown -R app:app /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/app \
    HOST=0.0.0.0 \
    PORT=8080 \
    # a private or missing repo must fail fast, never wait on a password prompt
    GIT_TERMINAL_PROMPT=0 \
    # small host disks: drop extracted trees after each run, cap the mirror cache
    FIXPOINT_EPHEMERAL_TREES=1 \
    # fetch the one commit a run needs instead of the repo's whole history:
    # django goes from an 11-minute clone to a 4-second one, 283 MB to 12 MB
    FIXPOINT_SHALLOW_CLONES=1 \
    FIXPOINT_CACHE_MAX_MB=2000 \
    FIXPOINT_MAX_CONCURRENT=1 \
    FIXPOINT_MAX_REPO_MB=400 \
    FIXPOINT_BACKEND=openai \
    FIXPOINT_BASE_URL=https://integrate.api.nvidia.com/v1

EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
