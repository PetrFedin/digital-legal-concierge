#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  python3 scripts/first_run.py
fi
mkdir -p storage logs backups
docker compose up -d --build
echo "Сервис запущен. Админка: http://localhost:8000/admin-ui"
echo "Проверка: ./status.sh"
