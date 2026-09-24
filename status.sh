#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
compose_files_raw="${COMPOSE_FILES:-${COMPOSE_FILE:-docker-compose.yml}}"
IFS=':' read -r -a compose_files <<< "$compose_files_raw"
compose_args=()
for compose_file in "${compose_files[@]}"; do
  [ -n "$compose_file" ] || continue
  compose_args+=(-f "$compose_file")
done
dc() { docker compose "${compose_args[@]}" "$@"; }

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


if dc config --services | grep -qx bot; then
  bot_container_id="$(dc ps -q bot)"
  [ -n "$bot_container_id" ] || { echo "Контейнер bot не найден"; exit 2; }
  bot_revision="$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$bot_container_id")"
  echo "bot revision $bot_revision"
  [ "$bot_revision" = "$image_revision" ] || {
    echo "Web и Telegram worker запущены из разных commit SHA"
    exit 2
  }
  echo "telegram worker probe"
  dc exec -T bot python scripts/telegram_worker_probe.py
fi

for service in app postgres redis; do
  cid="$(dc ps -q "$service")"
  [ -n "$cid" ] || { echo "Контейнер $service не найден"; exit 2; }
  restart_policy="$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$cid")"
  echo "$service restart=$restart_policy"
  [ "$restart_policy" = "unless-stopped" ] || exit 2
done
if dc config --services | grep -qx bot; then
  cid="$(dc ps -q bot)"
  restart_policy="$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$cid")"
  echo "bot restart=$restart_policy"
  [ "$restart_policy" = "unless-stopped" ] || exit 2
fi
