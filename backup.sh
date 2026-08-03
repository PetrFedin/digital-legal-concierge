#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
compose_file="${COMPOSE_FILE:-docker-compose.yml}"
docker compose -f "$compose_file" exec -T app \
  python -m app.security.backup_cli create "$@"
