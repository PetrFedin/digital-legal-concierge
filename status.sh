#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
BASE_URL="${BASE_URL:-http://localhost:8000}"
echo "Проверяю сервис: $BASE_URL"
python scripts/self_check.py || true
if [ -f .env ]; then
  ADMIN_TOKEN=$(grep -E '^ADMIN_API_TOKEN=' .env | cut -d= -f2- || true)
  if [ -n "${ADMIN_TOKEN:-}" ]; then
    echo ""
    echo "Снимок системы:"
    curl -s -H "x-admin-token: $ADMIN_TOKEN" "$BASE_URL/runtime/snapshot" | python -m json.tool || true
  fi
fi
