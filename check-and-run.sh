#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
. .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
python -m pip install -e . >/dev/null
if [ ! -f .env ]; then python scripts/first_run.py; fi
python scripts/full_check_v14.py
python -m app.main
