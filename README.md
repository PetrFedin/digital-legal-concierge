# Digital Legal Concierge Telegram Bot v43

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


## Контролируемые статусы и воспроизводимый калькулятор

Статусы дела меняются только через единый `CaseService` и явный граф допустимых переходов. Повтор одного статуса идемпотентен и не создаёт дубли истории или SLA. Принудительное исправление доступно только административному или системному процессу с обязательным комментарием и отметкой `forced` в аудите.

Расчёт неустойки выполняется только на явно переданную дату с `Decimal`-арифметикой. В базе сохраняются дата расчёта, применённая ключевая ставка, коэффициент и версия формулы, поэтому результат можно воспроизвести после изменения настроек. Значение `LEGAL_KEY_RATE` задаётся долей: `0.16` означает 16%.

`python scripts/architecture_check.py` блокирует слияние при прямой записи `case.status`, несанкционированном `force=True`, пользовательских `noop`-кнопках или дублирующихся HTTP method/path.

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

## Безопасность платёжных webhook

Все входящие события проходят через таблицу `payment_webhook_events`. Один provider event обрабатывается только один раз; повтор после завершения возвращает идемпотентный ответ и не создаёт повторных переходов статуса, уведомлений или проводок.

Одинаковый event ID с изменённым телом переводится в `DEAD_LETTER`, возвращает `409` и регистрируется в Security Center как возможная replay-подмена. Тело запроса ограничивается до разбора JSON, зависшие события допускают контролируемый retry, а число попыток ограничено.

В базе сохраняются SHA-256 исходного канонического JSON и минимизированная сводка. Полные webhook/API payload, реквизиты карты, email клиента и иные лишние персональные данные в историю дела не записываются. Для YooKassa итоговый статус, сумма, валюта и metadata дополнительно перепроверяются через API провайдера.

Production-настройки:

```text
PAYMENT_WEBHOOK_SECRET=<случайный секрет длиной не менее 32 символов>
MAX_PAYMENT_WEBHOOK_KB=256
PAYMENT_WEBHOOK_PROCESSING_TIMEOUT_SECONDS=300
PAYMENT_WEBHOOK_MAX_ATTEMPTS=8
```

## Reverse proxy и реальный IP клиента

По умолчанию приложение не доверяет `X-Forwarded-For`, `X-Real-IP` и `X-Forwarded-Proto`. Это защищает login throttling, Security Center и webhook-аудит от подмены IP внешним клиентом.

В production укажите только фактические внутренние адреса ingress, nginx или load balancer:

```text
TRUSTED_PROXY_CIDRS=10.10.0.0/16,172.20.0.10/32
TRUSTED_PROXY_MAX_HOPS=5
TRUST_FORWARDED_PROTO=true
```

Не указывайте `0.0.0.0/0` или `::/0`. Цепочка разбирается справа налево до ближайшего недоверенного адреса. Если CIDR, IP, длина цепочки или forwarded proto некорректны, приложение использует прямой peer IP, отклоняет заголовок и отражает проблему в `/ready` или Security Center.

При прямом подключении приложения к интернету без reverse proxy оставьте `TRUSTED_PROXY_CIDRS` пустым.

## Перед реальным запуском

Заполните `.env`, выполните `alembic upgrade head`, убедитесь, что `/ready` возвращает `ok=true`, создайте и проверьте первую `.dlcbak` копию. Для старых plaintext `.tar.gz/.zip/.db` Backup Center показывает отдельное предупреждение: они не допускаются к восстановлению новым механизмом.
