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

container_id="$(dc ps -q app)"
[ -n "$container_id" ] || { echo "Контейнер app не найден"; exit 2; }
image_reference="$(docker inspect --format '{{.Config.Image}}' "$container_id")"
image_revision="$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$container_id")"
echo "image $image_reference"
echo "revision $image_revision"

dc exec -T -e EXPECTED_COMMIT="$image_revision" app python - <<'PY'
import json
import os
import urllib.request

all_ok = True
payloads = {}
for path in ("/health", "/ready", "/runtime/release"):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=10) as response:
            payload = json.load(response)
        payloads[path] = payload
        endpoint_ok = payload.get("ok") is True
        all_ok = all_ok and endpoint_ok
        print(path, json.dumps(payload, ensure_ascii=False, sort_keys=True))
    except Exception as error:
        all_ok = False
        print(path, "ERROR", type(error).__name__, str(error))

release = payloads.get("/runtime/release", {})
expected = os.environ.get("EXPECTED_COMMIT", "")
identity_ok = bool(expected) and release.get("git_commit") == expected
print("release_identity_matches_image", identity_ok)
all_ok = all_ok and identity_ok
raise SystemExit(0 if all_ok else 2)
PY
