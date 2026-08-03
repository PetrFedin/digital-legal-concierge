#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
compose_file="${COMPOSE_FILE:-docker-compose.yml}"
docker compose -f "$compose_file" run --rm --no-deps \
  --entrypoint python app scripts/production_acceptance.py
