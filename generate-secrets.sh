#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

output="${1:-.env.generated.secrets}"
umask 077

names=(
  ADMIN_API_TOKEN
  ADMIN_PASSWORD
  SESSION_SIGNING_KEY
  SECURITY_HMAC_KEY
  MFA_ENCRYPTION_KEY
  AUDIT_INTEGRITY_KEY
  DOCUMENT_ENCRYPTION_KEY
  BACKUP_ENCRYPTION_KEY
  PAYMENT_WEBHOOK_SECRET
)

write_with_openssl() {
  command -v openssl >/dev/null 2>&1 || return 1
  : > "$output"
  for name in "${names[@]}"; do
    printf '%s=%s\n' "$name" "$(openssl rand -hex 48)" >> "$output"
  done
}

write_with_python_container() {
  docker run --rm python:3.11-bookworm python -c '
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
}

rm -f "$output"
if write_with_openssl; then
  generator="openssl"
elif command -v docker >/dev/null 2>&1 && write_with_python_container; then
  generator="docker/python:3.11-bookworm"
else
  rm -f "$output"
  echo "Не удалось создать секреты: установите openssl или обеспечьте доступ Docker к python:3.11-bookworm." >&2
  exit 1
fi

line_count="$(wc -l < "$output" | tr -d ' ')"
if [ "$line_count" != "${#names[@]}" ]; then
  rm -f "$output"
  echo "Генератор создал неполный файл секретов: $line_count строк вместо ${#names[@]}." >&2
  exit 1
fi

chmod 600 "$output"
echo "Секреты созданы через $generator и записаны в $output. Перенесите значения в .env и удалите файл после настройки."
