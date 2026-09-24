#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
compose_files_raw="${COMPOSE_FILES:-${COMPOSE_FILE:-docker-compose.yml}}"
IFS=':' read -r -a compose_files <<< "$compose_files_raw"
compose_args=()
for compose_file in "${compose_files[@]}"; do
  [ -n "$compose_file" ] || continue
  compose_args+=(-f "$compose_file")
done
dc() { docker compose "${compose_args[@]}" "$@"; }

dc exec -T app python -m app.security.backup_cli create "$@"
