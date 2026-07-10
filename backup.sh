#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p backups
TS=$(date +%Y%m%d_%H%M%S)
ARCHIVE="backups/legal_bot_backup_${TS}.tar.gz"
tar -czf "$ARCHIVE" legal_bot.db storage .env 2>/dev/null || tar -czf "$ARCHIVE" storage .env
printf 'Backup создан: %s\n' "$ARCHIVE"
