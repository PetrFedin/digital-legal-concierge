#!/usr/bin/env bash
set -euo pipefail

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Создан .env из .env.example"
fi

python -m pip install -e .
python scripts/init_db.py
python scripts/e2e_smoke.py
python scripts/doctor.py || true

echo ""
echo "Готово. Запуск:"
echo "python -m app.main"
echo "Админ-панель: http://localhost:8000/admin-ui"
