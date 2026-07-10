#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
. .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
python -m pip install -e . >/dev/null

if [ ! -f .env ]; then
  echo "Первый запуск: создаю .env через мастер настройки."
  python scripts/first_run.py
fi

mkdir -p storage logs backups
python scripts/preflight_v19.py || true
python scripts/launch_check.py
python scripts/init_db.py
python scripts/operator_check.py
python scripts/e2e_smoke.py
python scripts/full_check_v20.py

echo ""
echo "Сервис v20 готов к запуску."
echo "Health:  http://localhost:8000/health-center/ui
Diag:    http://localhost:8000/diagnostic-center/ui
Recover: http://localhost:8000/recovery-center/ui
Wizard:  http://localhost:8000/install-wizard/ui
Launch:  http://localhost:8000/launch-assistant
Передача: http://localhost:8000/handover
Оператор: http://localhost:8000/operator
Админка:  http://localhost:8000/admin-ui"
echo "Health:  http://localhost:8000/health"
echo "Ready:   http://localhost:8000/ready"
echo "Управление: ./bot-control.sh"
echo ""
python -m app.main
