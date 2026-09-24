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
[ "${#compose_args[@]}" -gt 0 ] || {
  echo "ОШИБКА: не задан ни один compose-файл" >&2
  exit 1
}

state_dir="${RELEASE_STATE_DIR:-.release}"
current_state="$state_dir/current.env"
previous_state="$state_dir/previous.env"
candidate_state="$state_dir/rollback-candidate.env"
target_state="$state_dir/target.env"
dc() { docker compose "${compose_args[@]}" "$@"; }

fail() {
  echo "ОШИБКА: $*" >&2
  exit 1
}

safe_value() {
  case "$1" in
    ""|*[!A-Za-z0-9._/:,@+-]*) return 1 ;;
    *) return 0 ;;
  esac
}

state_value() {
  local file="$1"
  local key="$2"
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$file"
}

write_state() {
  local path="$1"
  local repository="$2"
  local tag="$3"
  local release="$4"
  local commit="$5"
  local built_at="$6"
  local migration_heads="$7"
  for value in "$repository" "$tag" "$release" "$commit" "$built_at" "$migration_heads"; do
    safe_value "$value" || fail "Недопустимое значение release state"
  done
  umask 077
  cat > "$path" <<EOF
APP_IMAGE_REPOSITORY=$repository
APP_IMAGE_TAG=$tag
APP_RELEASE=$release
GIT_COMMIT_SHA=$commit
BUILD_TIMESTAMP=$built_at
MIGRATION_HEADS=$migration_heads
EOF
  chmod 600 "$path"
}

wait_for_health() {
  local attempts="${1:-45}"
  local attempt=1
  while [ "$attempt" -le "$attempts" ]; do
    if dc exec -T app python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)" >/dev/null 2>&1; then
      return 0
    fi
    sleep 4
    attempt=$((attempt + 1))
  done
  return 1
}

verify_runtime_release() {
  local expected_commit="$1"
  dc exec -T -e EXPECTED_COMMIT="$expected_commit" app python - <<'PY'
import json
import os
import urllib.request

expected = os.environ["EXPECTED_COMMIT"]
with urllib.request.urlopen("http://127.0.0.1:8000/runtime/release", timeout=10) as response:
    payload = json.load(response)
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
raise SystemExit(
    0
    if payload.get("ok") is True and payload.get("git_commit") == expected
    else 2
)
PY
}


verify_telegram_worker() {
  if ! dc config --services | grep -qx bot; then
    return 0
  fi

  local attempts="${1:-30}"
  local attempt=1
  while [ "$attempt" -le "$attempts" ]; do
    if dc ps --status running --services 2>/dev/null | grep -qx bot; then
      if dc exec -T bot python scripts/telegram_worker_probe.py >/tmp/dlc-telegram-probe.log 2>&1; then
        cat /tmp/dlc-telegram-probe.log
        rm -f /tmp/dlc-telegram-probe.log
        return 0
      fi
    fi
    sleep 4
    attempt=$((attempt + 1))
  done

  cat /tmp/dlc-telegram-probe.log 2>/dev/null || true
  rm -f /tmp/dlc-telegram-probe.log
  return 1
}

verify_readiness() {
  dc exec -T app python - <<'PY'
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/ready", timeout=10) as response:
    payload = json.load(response)
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
raise SystemExit(0 if payload.get("ok") is True else 2)
PY
}

rollback_candidate() {
  [ -f "$candidate_state" ] || return 1
  local old_heads
  old_heads="$(state_value "$candidate_state" MIGRATION_HEADS)"
  if [ "$old_heads" = "unknown" ] || [ "$old_heads" != "$new_migration_heads" ]; then
    echo "Автоматический rollback заблокирован: Alembic-head отличается или неизвестен." >&2
    return 1
  fi

  echo "Новая версия не прошла проверку. Возвращаю предыдущий immutable image..."
  (
    set -a
    # shellcheck disable=SC1090
    . "$candidate_state"
    set +a
    rollback_services=(app)
    if dc config --services | grep -qx bot; then
      rollback_services+=(bot)
    fi
    dc up -d --no-deps --no-build --force-recreate "${rollback_services[@]}"
    wait_for_health 30
    verify_runtime_release "$GIT_COMMIT_SHA"
    verify_readiness
    verify_telegram_worker 30
  )
}

command -v git >/dev/null 2>&1 || fail "Git не установлен"
command -v docker >/dev/null 2>&1 || fail "Docker не установлен"
docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin не установлен"
[ -f .env ] || fail "Нет .env. Скопируйте .env.production.example и заполните значения."
for compose_file in "${compose_files[@]}"; do
  [ -f "$compose_file" ] || fail "Не найден compose-файл: $compose_file"
done
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "Деплой разрешён только из Git checkout"

expected_branch="${DEPLOY_BRANCH:-main}"
current_branch="$(git branch --show-current)"
if [ "${ALLOW_NON_MAIN_DEPLOY:-false}" != "true" ] && [ "$current_branch" != "$expected_branch" ]; then
  fail "Текущая ветка '$current_branch', требуется '$expected_branch'"
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  fail "Есть незакоммиченные изменения. Production deploy должен быть воспроизводимым."
fi

export GIT_COMMIT_SHA="$(git rev-parse --verify HEAD)"
export APP_IMAGE_REPOSITORY="${APP_IMAGE_REPOSITORY:-digital-legal-concierge}"
export APP_IMAGE_TAG="${RELEASE_TAG:-${GIT_COMMIT_SHA:0:12}}"
export APP_RELEASE="${APP_RELEASE:-$APP_IMAGE_TAG}"
export BUILD_TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
for value in "$APP_IMAGE_REPOSITORY" "$APP_IMAGE_TAG" "$APP_RELEASE" "$GIT_COMMIT_SHA" "$BUILD_TIMESTAMP"; do
  safe_value "$value" || fail "Недопустимое значение release metadata"
done

mkdir -p "$state_dir"
chmod 700 "$state_dir"
rm -f "$candidate_state" "$target_state"

if [ -f "$current_state" ]; then
  cp "$current_state" "$candidate_state"
  chmod 600 "$candidate_state"
elif dc ps --status running --services 2>/dev/null | grep -qx app; then
  running_container="$(dc ps -q app)"
  running_image_id="$(docker inspect --format '{{.Image}}' "$running_container")"
  bootstrap_tag="rollback-bootstrap-$(date -u +%Y%m%d%H%M%S)"
  docker tag "$running_image_id" "$APP_IMAGE_REPOSITORY:$bootstrap_tag"
  write_state \
    "$candidate_state" \
    "$APP_IMAGE_REPOSITORY" \
    "$bootstrap_tag" \
    "$bootstrap_tag" \
    "unknown" \
    "$BUILD_TIMESTAMP" \
    "unknown"
fi

 echo "Проверка Docker Compose..."
dc config --quiet

echo "Сборка immutable image $APP_IMAGE_REPOSITORY:$APP_IMAGE_TAG..."
dc build --pull app

image_revision="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$APP_IMAGE_REPOSITORY:$APP_IMAGE_TAG")"
[ "$image_revision" = "$GIT_COMMIT_SHA" ] || fail "OCI revision не совпадает с Git commit"

new_migration_heads="$(
  dc run --rm --no-deps --entrypoint python app \
    scripts/release_metadata.py --field migration_heads
)"
safe_value "$new_migration_heads" || fail "Не удалось определить Alembic-head нового image"
write_state \
  "$target_state" \
  "$APP_IMAGE_REPOSITORY" \
  "$APP_IMAGE_TAG" \
  "$APP_RELEASE" \
  "$GIT_COMMIT_SHA" \
  "$BUILD_TIMESTAMP" \
  "$new_migration_heads"

echo "Production preflight внутри нового image..."
dc run --rm --no-deps --entrypoint python app scripts/production_preflight.py

if dc ps --status running --services 2>/dev/null | grep -qx app; then
  echo "Создание зашифрованной резервной копии перед обновлением..."
  dc exec -T app python -m app.security.backup_cli create
fi

echo "Запуск release $APP_RELEASE..."
dc up -d --remove-orphans

deploy_ok=true
if [ "$(dc exec -T redis redis-cli ping 2>/dev/null || true)" != "PONG" ]; then
  echo "Redis FSM storage не прошёл проверку после запуска." >&2
  deploy_ok=false
fi
if ! wait_for_health "${DEPLOY_HEALTH_ATTEMPTS:-45}"; then
  echo "Сервис не прошёл liveness-проверку." >&2
  deploy_ok=false
fi
if [ "$deploy_ok" = "true" ] && ! verify_readiness; then
  echo "Production readiness не пройдена." >&2
  deploy_ok=false
fi
if [ "$deploy_ok" = "true" ] && ! verify_runtime_release "$GIT_COMMIT_SHA"; then
  echo "Работающий контейнер не соответствует ожидаемому Git commit." >&2
  deploy_ok=false
fi
if [ "$deploy_ok" = "true" ] && ! verify_telegram_worker "${DEPLOY_TELEGRAM_ATTEMPTS:-30}"; then
  echo "Telegram worker не прошёл identity/polling-lease проверку." >&2
  deploy_ok=false
fi

if [ "$deploy_ok" != "true" ]; then
  if dc config --services | grep -qx bot; then
    dc logs --tail=200 app redis bot || true
  else
    dc logs --tail=200 app redis || true
  fi
  if rollback_candidate; then
    echo "Предыдущий image восстановлен. Неуспешный release не записан как текущий." >&2
  else
    echo "Автоматический rollback невозможен. Используйте backup и staging restore по runbook." >&2
  fi
  rm -f "$target_state" "$candidate_state"
  exit 1
fi

if [ -f "$candidate_state" ]; then
  mv "$candidate_state" "$previous_state"
else
  rm -f "$previous_state"
fi
mv "$target_state" "$current_state"
chmod 600 "$current_state"
[ ! -f "$previous_state" ] || chmod 600 "$previous_state"

echo "Деплой завершён: $APP_RELEASE ($GIT_COMMIT_SHA)."
echo "Release state: $current_state"
dc ps
