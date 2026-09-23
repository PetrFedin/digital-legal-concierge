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
  3|deploy|start) COMPOSE_FILES="$compose_files_raw" bash ./deploy.sh ;;
  4|restart)
    services=(app)
    if dc config --services | grep -qx bot; then services+=(bot); fi
    dc restart "${services[@]}"
    ;;
  5|status) COMPOSE_FILES="$compose_files_raw" bash ./status.sh ;;
  6|logs)
    services=(app redis)
    if dc config --services | grep -qx bot; then services+=(bot); fi
    dc logs -f --tail=300 "${services[@]}"
    ;;
  7|backup) COMPOSE_FILES="$compose_files_raw" bash ./backup.sh ;;
  8|migrate) dc run --rm --no-deps --entrypoint python app scripts/init_db.py ;;
  9|accept) COMPOSE_FILES="$compose_files_raw" bash ./acceptance.sh ;;
  10|test) bash ./test.sh ;;
  11|rollback) COMPOSE_FILES="$compose_files_raw" bash ./rollback.sh ;;
  12|stop) dc down ;;
  13|secrets) bash ./generate-secrets.sh ;;
  0|exit) exit 0 ;;
  *) echo "Неизвестная команда: $choice"; exit 1 ;;
esac
