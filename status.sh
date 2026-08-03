#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
compose_file="${COMPOSE_FILE:-docker-compose.yml}"
dc() { docker compose -f "$compose_file" "$@"; }

dc ps

redis_status="$(dc exec -T redis redis-cli ping)"
if [ "$redis_status" != "PONG" ]; then
  echo "Redis FSM storage недоступен: $redis_status"
  exit 2
fi
echo "redis PONG"

dc exec -T app python - <<'PY'
import json
import urllib.request

all_ok = True
for path in ("/health", "/ready"):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=10) as response:
            payload = json.load(response)
        endpoint_ok = payload.get("ok") is True
        all_ok = all_ok and endpoint_ok
        print(path, json.dumps(payload, ensure_ascii=False, sort_keys=True))
    except Exception as error:
        all_ok = False
        print(path, "ERROR", type(error).__name__, str(error))
raise SystemExit(0 if all_ok else 2)
PY
