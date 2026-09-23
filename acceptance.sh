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

COMPOSE_FILES="$compose_files_raw" bash ./status.sh

dc run --rm --no-deps \
  --entrypoint python app scripts/production_acceptance.py
