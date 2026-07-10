#!/usr/bin/env bash
set -e
if [ ! -f .env ]; then
  cp .env.example .env
  echo 'Создан .env. Вставьте BOT_TOKEN и запустите еще раз.'
  exit 1
fi
python scripts/init_db.py
python -m app.main
