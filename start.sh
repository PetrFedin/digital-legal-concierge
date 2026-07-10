#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  ./install.sh
fi
. .venv/bin/activate
python scripts/init_db.py
python -m app.main
