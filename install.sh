#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python scripts/setup_env.py
mkdir -p storage logs
python scripts/init_db.py
python scripts/doctor.py
python scripts/e2e_smoke.py

echo ""
echo "Установка завершена. Теперь запустите: ./run.sh"
