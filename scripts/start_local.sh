#!/usr/bin/env bash
set -e
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Создан .env из .env.example. Вставьте BOT_TOKEN, если хотите запустить Telegram-бота."
fi
python scripts/init_db.py
python -m app.main
