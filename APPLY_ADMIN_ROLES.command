#!/bin/bash
set -euo pipefail

UPDATE_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$UPDATE_DIR/.." && pwd)"

if [ ! -d "$PROJECT_DIR/app" ] || [ ! -f "$PROJECT_DIR/docker-compose.yml" ]; then
  echo ""
  echo "ОШИБКА: папка обновления должна находиться внутри папки проекта."
  echo ""
  echo "Переместите папку admin_roles_update в:"
  echo "  /Users/petr/Downloads/tg_ready_v30 2/"
  echo ""
  echo "Затем снова откройте APPLY_ADMIN_ROLES.command"
  echo ""
  read -n 1 -s -r -p "Нажмите любую клавишу..."
  exit 1
fi

cd "$PROJECT_DIR"
BACKUP_DIR="backups/admin_roles_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

FILES=(
  "app/api/access_management.py"
  "app/api/auth.py"
  "app/api/admin.py"
  "app/api/web_admin.py"
  "app/models/admin_user.py"
  "app/security/__init__.py"
  "app/security/access_control.py"
  "app/main.py"
  "scripts/init_db.py"
)

for file in "${FILES[@]}"; do
  if [ -f "$file" ]; then
    mkdir -p "$BACKUP_DIR/$(dirname "$file")"
    cp "$file" "$BACKUP_DIR/$file"
  fi
done

cp -R "$UPDATE_DIR/patch/app/." "$PROJECT_DIR/app/"
cp -R "$UPDATE_DIR/patch/scripts/." "$PROJECT_DIR/scripts/"

chmod +x START_BOT.command STOP_BOT.command BOT_STATUS.command 2>/dev/null || true

echo ""
echo "Останавливаю текущий контейнер..."
docker compose down

echo ""
echo "Пересобираю проект с совмещаемыми правами..."
docker compose build --no-cache

echo ""
echo "Запускаю проект..."
docker compose up -d

echo ""
echo "Жду инициализацию базы..."
sleep 8

if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
  echo ""
  echo "ГОТОВО: права администратора, суперадминистратора и юриста установлены."
  echo ""
  echo "Вход:                 http://localhost:8000/login"
  echo "Админ-панель:         http://localhost:8000/admin-ui"
  echo "Пользователи и права:  http://localhost:8000/access/ui"
  echo ""
  echo "Первый пользователь: логин admin"
  echo "Пароль: текущее значение ADMIN_PASSWORD из файла .env"
  echo ""
  echo "Резервная копия изменённых файлов: $BACKUP_DIR"
else
  echo ""
  echo "Сервис не ответил. Последние логи:"
  docker compose logs --tail=120
  echo ""
  echo "Резервная копия находится в: $BACKUP_DIR"
  exit 1
fi

echo ""
read -n 1 -s -r -p "Нажмите любую клавишу, чтобы закрыть окно..."
echo
