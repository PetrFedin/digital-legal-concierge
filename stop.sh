#!/usr/bin/env bash
set -euo pipefail
if command -v docker >/dev/null 2>&1; then
  docker compose down || true
fi
pkill -f "python -m app.main" || true
echo "Остановлено."
