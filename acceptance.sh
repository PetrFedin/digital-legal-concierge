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

dc run --rm --no-deps   --entrypoint python app scripts/production_acceptance.py

if dc config --services | grep -qx bot; then
  dc exec -T bot python scripts/telegram_worker_probe.py
fi
