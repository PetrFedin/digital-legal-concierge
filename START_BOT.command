#!/bin/bash
set -e
cd "$(dirname "$0")"
clear
printf '\nDigital Legal Concierge - запуск Telegram-бота\n\n'
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker не найден."
  echo "1. Установите Docker Desktop: https://www.docker.com/products/docker-desktop/"
  echo "2. Запустите Docker Desktop."
  echo "3. Снова дважды нажмите START_BOT.command."
  echo
  read -n 1 -s -r -p "Нажмите любую клавишу для выхода..."
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop установлен, но не запущен."
  echo "Откройте Docker Desktop, дождитесь запуска и повторите."
  echo
  read -n 1 -s -r -p "Нажмите любую клавишу для выхода..."
  exit 1
fi
mkdir -p data storage logs backups
echo "Собираю и запускаю бота..."
docker compose up -d --build
echo
echo "Проверяю состояние..."
for i in {1..30}; do
  if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
    echo "Готово: сервер и Telegram-бот запущены."
    echo
    echo "Админ-панель: http://localhost:8000/admin-ui"
    echo "Проверка:      http://localhost:8000/health"
    echo
    echo "Теперь откройте Telegram, найдите своего бота и отправьте /start"
    echo
    read -n 1 -s -r -p "Нажмите любую клавишу, чтобы закрыть окно..."
    exit 0
  fi
  sleep 2
done
echo "Сервис не ответил вовремя. Последние логи:"
docker compose logs --tail=120 app
read -n 1 -s -r -p "Нажмите любую клавишу, чтобы закрыть окно..."
exit 1
