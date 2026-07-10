#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if command -v docker >/dev/null 2>&1 && docker compose ps >/dev/null 2>&1; then
  docker compose logs -f --tail=200 app
else
  echo "Если запускали ./run.sh, логи идут в текущем терминале."
  echo "Для Docker: docker compose logs -f --tail=200 app"
fi
