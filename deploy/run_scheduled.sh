#!/usr/bin/env bash
# Scheduled entry point (cron or systemd timer). Usage: deploy/run_scheduled.sh [scraper args...]
#   deploy/run_scheduled.sh                 full live run + Excel/CSV
#   deploy/run_scheduled.sh --limit 20      quick run
# It starts the P6 Postgres container if needed, runs `run-all`, and exits with the CLI's code
# (2 = interrupted; the next scheduled run resumes from the checkpoint).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p outputs/logs

# Docker Desktop may still be starting after a Windows login: wait up to ~2 min.
for _ in $(seq 1 24); do docker info >/dev/null 2>&1 && break; sleep 5; done
docker compose up -d --wait db >/dev/null

# Only one run at a time (a slow run must not overlap the next trigger).
exec 9>outputs/.scheduled.lock
flock -n 9 || { echo "another scheduled run is still active; skipping"; exit 0; }

.venv/bin/python -m scraper run-all "$@"
