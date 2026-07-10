#!/bin/bash
cd "$(dirname "$0")"
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker не установлен."
else
  docker compose ps
  echo
  docker compose logs --tail=80 app
fi
read -n 1 -s -r -p "Нажмите любую клавишу, чтобы закрыть окно..."
