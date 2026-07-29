#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Creates only a .dlcbak container encrypted with BACKUP_ENCRYPTION_KEY.
# The temporary plaintext payload is created with mode 0600 and always removed.
# .env and other secret files are deliberately excluded from the archive.
exec python -m app.security.backup_cli create "$@"
