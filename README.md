# Digital Legal Concierge Telegram Bot v40

Telegram-бот и административный контур для сопровождения клиентов по взысканию неустойки по ДДУ 214-ФЗ.

## Быстрый запуск

```bash
./run.sh
```

## Управление

```bash
./bot-control.sh
```

## Главные страницы

- http://localhost:8000/maintenance-center/ui
- http://localhost:8000/operator
- http://localhost:8000/admin-ui
- http://localhost:8000/lawyer/ui
- http://localhost:8000/document-access/ui
- http://localhost:8000/security-events/ui
- http://localhost:8000/audit-center/ui
- http://localhost:8000/backup-center/ui
- http://localhost:8000/ready

## Проверка

```bash
./acceptance.sh
python scripts/full_check_v30.py
pytest -q
```

## Обязательные production-ключи

Для production задаются шесть независимых случайных ключей длиной не менее 32 символов:

```text
SESSION_SIGNING_KEY
SECURITY_HMAC_KEY
MFA_ENCRYPTION_KEY
AUDIT_INTEGRITY_KEY
DOCUMENT_ENCRYPTION_KEY
BACKUP_ENCRYPTION_KEY
```

Каждый контур имеет собственный `*_KEY_ID` и `*_PREVIOUS_KEYS` для безопасной ротации. Нельзя использовать один секрет в нескольких контурах или совпадение с `ADMIN_API_TOKEN`.

## Зашифрованные резервные копии

Создание согласованной SQLite-копии базы и хранилища документов:

```bash
./backup.sh
```

Результат сохраняется только в формате `backups/legal_concierge_YYYYMMDD_HHMMSS.dlcbak`. Контейнер зашифрован AES-256-GCM и после создания автоматически проходит полную проверку AEAD и manifest SHA-256.

Проверка существующей копии:

```bash
python -m app.security.backup_cli verify backups/legal_concierge_YYYYMMDD_HHMMSS.dlcbak
```

Безопасное восстановление выполняется только в пустой staging-каталог:

```bash
./restore.sh backups/legal_concierge_YYYYMMDD_HHMMSS.dlcbak /tmp/legal-concierge-restore
```

Команда не перезаписывает работающую базу или каталог приложения. После проверки сервис необходимо остановить и перенести восстановленные `database/` и `storage/` по утверждённой эксплуатационной процедуре.

`.env`, токены, криптографические ключи, временные загрузки и quarantine в backup не включаются. Production-секреты должны храниться отдельно в secret manager и в независимом защищённом recovery-комплекте.

## Перед реальным запуском

Заполните `.env`, выполните `alembic upgrade head`, убедитесь, что `/ready` возвращает `ok=true`, создайте и проверьте первую `.dlcbak` копию. Для старых plaintext `.tar.gz/.zip/.db` Backup Center показывает отдельное предупреждение: они не допускаются к восстановлению новым механизмом.
