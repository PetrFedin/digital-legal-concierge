#!/bin/sh
set -eu

cd /app
mkdir -p /app/data /app/storage /app/logs /app/backups

python scripts/production_preflight.py
python scripts/wait_for_redis.py
python scripts/wait_for_database.py
python scripts/init_db.py
python scripts/ensure_startup_backup.py

exec "$@"
