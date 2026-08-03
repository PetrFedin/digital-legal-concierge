#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

output="${1:-.env.generated.secrets}"
umask 077

docker run --rm python:3.11-slim python -c '
import secrets
names = [
    "ADMIN_API_TOKEN",
    "ADMIN_PASSWORD",
    "SESSION_SIGNING_KEY",
    "SECURITY_HMAC_KEY",
    "MFA_ENCRYPTION_KEY",
    "AUDIT_INTEGRITY_KEY",
    "DOCUMENT_ENCRYPTION_KEY",
    "BACKUP_ENCRYPTION_KEY",
    "PAYMENT_WEBHOOK_SECRET",
]
for name in names:
    print(f"{name}={secrets.token_urlsafe(48)}")
' > "$output"
chmod 600 "$output"
echo "Секреты записаны в $output. Перенесите значения в .env и удалите файл после настройки."
