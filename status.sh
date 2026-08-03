#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
compose_file="${COMPOSE_FILE:-docker-compose.yml}"
dc() { docker compose -f "$compose_file" "$@"; }

dc ps

dc exec -T app python - <<'PY'
import json
import urllib.request

for path in ("/health", "/ready"):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=10) as response:
            payload = json.load(response)
        print(path, json.dumps(payload, ensure_ascii=False, sort_keys=True))
    except Exception as error:
        print(path, "ERROR", type(error).__name__, str(error))
PY
