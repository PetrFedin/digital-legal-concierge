#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

compose_file="${COMPOSE_FILE:-docker-compose.yml}"
state_dir="${RELEASE_STATE_DIR:-.release}"
current_state="$state_dir/current.env"
previous_state="$state_dir/previous.env"
dc() { docker compose -f "$compose_file" "$@"; }

fail() {
  echo "ОШИБКА: $*" >&2
  exit 1
}

state_value() {
  local file="$1"
  local key="$2"
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$file"
}

wait_for_health() {
  local attempt=1
  local attempts="${ROLLBACK_HEALTH_ATTEMPTS:-30}"
  while [ "$attempt" -le "$attempts" ]; do
    if dc exec -T app python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)" >/dev/null 2>&1; then
      return 0
    fi
    sleep 4
    attempt=$((attempt + 1))
  done
  return 1
}

verify_release_and_readiness() {
  local expected_commit="$1"
  dc exec -T -e EXPECTED_COMMIT="$expected_commit" app python - <<'PY'
import json
import os
import urllib.request

expected = os.environ["EXPECTED_COMMIT"]
with urllib.request.urlopen("http://127.0.0.1:8000/runtime/release", timeout=10) as response:
    release = json.load(response)
with urllib.request.urlopen("http://127.0.0.1:8000/ready", timeout=10) as response:
    ready = json.load(response)
print(json.dumps({"release": release, "ready": ready}, ensure_ascii=False, sort_keys=True))
raise SystemExit(
    0
    if release.get("ok") is True
    and release.get("git_commit") == expected
    and ready.get("ok") is True
    else 2
)
PY
}

command -v docker >/dev/null 2>&1 || fail "Docker не установлен"
docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin не установлен"
[ -f "$current_state" ] || fail "Нет текущего release state: $current_state"
[ -f "$previous_state" ] || fail "Нет предыдущего release state: $previous_state"
[ -f "$compose_file" ] || fail "Не найден compose-файл: $compose_file"

current_heads="$(state_value "$current_state" MIGRATION_HEADS)"
previous_heads="$(state_value "$previous_state" MIGRATION_HEADS)"
if [ "$current_heads" = "unknown" ] || [ "$previous_heads" = "unknown" ]; then
  fail "Rollback заблокирован: Alembic-head неизвестен"
fi
if [ "$current_heads" != "$previous_heads" ]; then
  fail "Rollback заблокирован: Alembic-head отличается. Используйте staging restore backup."
fi

previous_repository="$(state_value "$previous_state" APP_IMAGE_REPOSITORY)"
previous_tag="$(state_value "$previous_state" APP_IMAGE_TAG)"
previous_commit="$(state_value "$previous_state" GIT_COMMIT_SHA)"
docker image inspect "$previous_repository:$previous_tag" >/dev/null 2>&1 \
  || fail "Предыдущий immutable image отсутствует: $previous_repository:$previous_tag"

if dc ps --status running --services 2>/dev/null | grep -qx app; then
  echo "Создание backup текущей версии перед rollback..."
  dc exec -T app python -m app.security.backup_cli create
fi

echo "Rollback на $previous_repository:$previous_tag..."
if ! (
  set -a
  # shellcheck disable=SC1090
  . "$previous_state"
  set +a
  dc up -d --no-deps --no-build --force-recreate app
  wait_for_health
  verify_release_and_readiness "$GIT_COMMIT_SHA"
); then
  echo "Rollback image не прошёл проверки. Возвращаю исходный current image..." >&2
  (
    set -a
    # shellcheck disable=SC1090
    . "$current_state"
    set +a
    dc up -d --no-deps --no-build --force-recreate app
    wait_for_health
    verify_release_and_readiness "$GIT_COMMIT_SHA"
  ) || fail "Не удалось восстановить исходный image; требуется аварийная процедура"
  fail "Rollback отменён, исходный release восстановлен"
fi

swap_state="$state_dir/state-swap.env"
cp "$current_state" "$swap_state"
cp "$previous_state" "$current_state"
mv "$swap_state" "$previous_state"
chmod 600 "$current_state" "$previous_state"

echo "Rollback завершён. Текущий commit: $previous_commit"
dc ps
