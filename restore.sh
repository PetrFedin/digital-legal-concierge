#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ "$#" -ne 2 ]; then
  echo "Использование: ./restore.sh /полный/путь/backup.dlcbak /полный/путь/пустой-staging"
  exit 1
fi

archive="$(realpath "$1")"
destination="$2"
mkdir -p "$destination"
destination="$(realpath "$destination")"

if [ -n "$(find "$destination" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo "Staging-каталог должен быть пустым: $destination"
  exit 1
fi

compose_file="${COMPOSE_FILE:-docker-compose.yml}"
docker compose -f "$compose_file" run --rm --no-deps \
  --entrypoint python \
  -v "$archive:/restore/source.dlcbak:ro" \
  -v "$destination:/restore/output" \
  app -m app.security.backup_cli extract /restore/source.dlcbak /restore/output
