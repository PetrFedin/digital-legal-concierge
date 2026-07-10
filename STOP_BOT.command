#!/bin/bash
set -e
cd "$(dirname "$0")"
if command -v docker >/dev/null 2>&1; then
  docker compose down
fi
echo "Бот остановлен."
read -n 1 -s -r -p "Нажмите любую клавишу, чтобы закрыть окно..."
