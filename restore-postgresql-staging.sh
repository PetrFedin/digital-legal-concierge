#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ "$#" -ne 3 ]; then
  echo "Использование: STAGING_DATABASE_URL=... ./restore-postgresql-staging.sh <archive.dlcbak> <пустой staging-каталог> <имя staging-базы>"
  echo "Target-база должна быть пустой и иметь суффикс staging, restore, drill или test."
  exit 1
fi

if [ -z "${STAGING_DATABASE_URL:-}" ]; then
  echo "Ошибка: переменная STAGING_DATABASE_URL обязательна" >&2
  exit 1
fi

exec python -m app.security.backup_cli restore-postgresql-staging \
  "$1" "$2" --confirm-database "$3"
