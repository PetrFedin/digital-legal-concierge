#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

compose_file="${COMPOSE_FILE:-docker-compose.yml}"
accepted_sha="${DEPLOY_EXACT_SHA:-}"

if [ -z "$accepted_sha" ]; then
  echo "Ошибка: DEPLOY_EXACT_SHA обязателен для acceptance exact release" >&2
  exit 2
fi

case "$accepted_sha" in
  *[!0-9a-f]*|"")
    echo "Ошибка: DEPLOY_EXACT_SHA должен быть полным lowercase Git SHA" >&2
    exit 2
    ;;
esac
if [ "${#accepted_sha}" -ne 40 ]; then
  echo "Ошибка: DEPLOY_EXACT_SHA должен содержать ровно 40 символов" >&2
  exit 2
fi

current_sha="$(git rev-parse --verify HEAD)"
if [ "$current_sha" != "$accepted_sha" ]; then
  echo "Ошибка: checkout $current_sha не совпадает с accepted SHA $accepted_sha" >&2
  exit 2
fi

docker compose -f "$compose_file" exec -T app \
  python scripts/production_preflight.py

COMPOSE_FILE="$compose_file" DEPLOY_EXACT_SHA="$accepted_sha" bash ./status.sh
