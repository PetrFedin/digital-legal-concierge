#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

compose_file="${COMPOSE_FILE:-docker-compose.yml}"
dc() { docker compose -f "$compose_file" "$@"; }

command -v docker >/dev/null 2>&1 || { echo "Docker не установлен"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose plugin не установлен"; exit 1; }
[ -f .env ] || { echo "Нет .env. Скопируйте .env.production.example в .env и заполните значения."; exit 1; }
[ -f "$compose_file" ] || { echo "Не найден compose-файл: $compose_file"; exit 1; }

echo "Проверка Docker Compose..."
dc config --quiet

echo "Сборка нового образа..."
dc build --pull app

if dc ps --status running --services 2>/dev/null | grep -qx app; then
  echo "Создание резервной копии перед обновлением..."
  dc exec -T app python -m app.security.backup_cli create
fi

echo "Production preflight внутри нового образа..."
dc run --rm --no-deps --entrypoint python app scripts/production_preflight.py

echo "Запуск новой версии..."
dc up -d --remove-orphans

if [ "$(dc exec -T redis redis-cli ping 2>/dev/null || true)" != "PONG" ]; then
  echo "Redis FSM storage не прошёл проверку после запуска. Последние логи:"
  dc logs --tail=200 redis app
  exit 1
fi

attempt=1
max_attempts="${DEPLOY_HEALTH_ATTEMPTS:-45}"
while [ "$attempt" -le "$max_attempts" ]; do
  if dc exec -T app python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)" >/dev/null 2>&1; then
    break
  fi
  sleep 4
  attempt=$((attempt + 1))
done

if [ "$attempt" -gt "$max_attempts" ]; then
  echo "Сервис не прошёл liveness-проверку. Последние логи:"
  dc logs --tail=200 app redis
  exit 1
fi

if ! dc exec -T app python - <<'PY'
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/ready", timeout=10) as response:
    payload = json.load(response)
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
raise SystemExit(0 if payload.get("ok") is True else 2)
PY
then
  echo "Контейнер жив, но production readiness не пройдена. Последние логи:"
  dc logs --tail=200 app redis
  exit 1
fi

echo "Деплой завершён. Redis, /health и /ready прошли проверку."
dc ps
