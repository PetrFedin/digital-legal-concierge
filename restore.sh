#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ $# -ne 1 ]; then
  echo "Использование: ./restore.sh backups/legal_bot_backup_YYYYMMDD_HHMMSS.tar.gz"
  exit 1
fi
tar -xzf "$1"
echo "Восстановлено из $1"
