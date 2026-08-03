#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

compose_file="${COMPOSE_FILE:-docker-compose.yml}"
dc() { docker compose -f "$compose_file" "$@"; }

show_menu() {
  cat <<'EOF'

Digital Legal Concierge — Docker control
1) Проверить compose и production env
2) Собрать production-образ
3) Запустить / обновить immutable release
4) Перезапустить текущий контейнер
5) Показать status, release commit и readiness
6) Показать логи
7) Создать зашифрованный backup
8) Выполнить миграции и bootstrap
9) Выполнить production acceptance внутри Docker
10) Выполнить полный pytest-набор в отдельном Docker-образе
11) Выполнить schema-safe rollback на предыдущий image
12) Остановить сервис
13) Сгенерировать production-секреты
0) Выход
EOF
}

choice="${1:-menu}"
if [ "$choice" = "menu" ]; then
  show_menu
  read -r -p "Выберите действие: " choice
fi

case "$choice" in
  1|check)
    dc config --quiet
    dc run --rm --no-deps --entrypoint python app scripts/production_preflight.py
    ;;
  2|build) dc build --pull app ;;
  3|deploy|start) COMPOSE_FILE="$compose_file" bash ./deploy.sh ;;
  4|restart) dc restart app ;;
  5|status) COMPOSE_FILE="$compose_file" bash ./status.sh ;;
  6|logs) dc logs -f --tail=300 app redis ;;
  7|backup) COMPOSE_FILE="$compose_file" bash ./backup.sh ;;
  8|migrate) dc run --rm --no-deps --entrypoint python app scripts/init_db.py ;;
  9|accept) COMPOSE_FILE="$compose_file" bash ./acceptance.sh ;;
  10|test) bash ./test.sh ;;
  11|rollback) COMPOSE_FILE="$compose_file" bash ./rollback.sh ;;
  12|stop) dc down ;;
  13|secrets) bash ./generate-secrets.sh ;;
  0|exit) exit 0 ;;
  *) echo "Неизвестная команда: $choice"; exit 1 ;;
esac
