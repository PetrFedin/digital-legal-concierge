#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

compose_file="${COMPOSE_FILE:-docker-compose.yml}"
dc() { docker compose -f "$compose_file" "$@"; }

show_menu() {
  cat <<'EOF'

Digital Legal Concierge — Docker control
1) Проверить compose и production env
2) Собрать образ
3) Запустить / обновить
4) Перезапустить контейнер
5) Показать статус /health и /ready
6) Показать логи
7) Создать зашифрованный backup
8) Выполнить миграции и bootstrap
9) Полная pytest-проверка внутри Docker
10) Остановить сервис
11) Сгенерировать production-секреты
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
  3|deploy|start) COMPOSE_FILE="$compose_file" ./deploy.sh ;;
  4|restart) dc restart app ;;
  5|status) COMPOSE_FILE="$compose_file" ./status.sh ;;
  6|logs) dc logs -f --tail=300 app ;;
  7|backup) COMPOSE_FILE="$compose_file" ./backup.sh ;;
  8|migrate) dc run --rm --no-deps --entrypoint python app scripts/init_db.py ;;
  9|test) dc run --rm --no-deps --entrypoint pytest app -q ;;
  10|stop) dc down ;;
  11|secrets) ./generate-secrets.sh ;;
  0|exit) exit 0 ;;
  *) echo "Неизвестная команда: $choice"; exit 1 ;;
esac
