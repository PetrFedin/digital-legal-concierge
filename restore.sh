#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ "$#" -ne 2 ]; then
  echo "Использование: ./restore.sh backups/legal_concierge_YYYYMMDD_HHMMSS.dlcbak /пустой/staging-каталог"
  echo "Команда никогда не распаковывает архив поверх работающего приложения."
  exit 1
fi

exec python -m app.security.backup_cli extract "$1" "$2"
