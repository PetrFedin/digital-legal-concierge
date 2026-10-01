# Digital Legal Concierge Telegram Bot v45

Telegram-бот и административный контур для сопровождения клиентов по взысканию неустойки по ДДУ 214-ФЗ.

## Быстрый запуск через Docker

На сервере нужны только Git, Docker Engine и Docker Compose plugin. Python, pip, virtualenv,
PostgreSQL client и Redis отдельно устанавливать не требуется: они находятся внутри
контейнеров.

Локальный Docker-запуск:

```bash
cp .env.example .env
bash ./run.sh
```

Production-перезапуск на облачном сервере Timeweb:

```bash
cp .env.production.example .env
bash ./generate-secrets.sh
# заполните .env и удалите .env.generated.secrets
bash ./timeweb-deploy.sh
```

Полная процедура и ограничения: [`docs/TIMEWEB_RESTART.md`](docs/TIMEWEB_RESTART.md).

## Управление

```bash
bash ./bot-control.sh
```

Для Timeweb-команд:

```bash
COMPOSE_FILE=docker-compose.timeweb.yml bash ./bot-control.sh
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
- http://localhost:8000/retention/ui
- http://localhost:8000/ready

## Проверка

Production acceptance выполняется внутри контейнера:

```bash
bash ./acceptance.sh
```

Проверка состояния работающей версии:

```bash
bash ./status.sh
```

В development-окружении полный набор тестов также запускается внутри Docker-образа,
собранного с test-зависимостями; production-образ намеренно не содержит pytest.

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

Каждый контур имеет собственный `*_KEY_ID` и `*_PREVIOUS_KEYS` для безопасной ротации.
Нельзя использовать один секрет в нескольких контурах или совпадение с
`ADMIN_API_TOKEN`. `generate-secrets.sh` создаёт значения через одноразовый Docker-контейнер,
не требуя Python на хосте.

## Перезапуск и Telegram FSM

В production незавершённые пользовательские сценарии хранятся в Redis:

```text
FSM_STORAGE_BACKEND=redis
REDIS_URL=redis://redis:6379/0
REDIS_STARTUP_WAIT_SECONDS=60
```

Redis запускается без опубликованного наружу порта, использует AOF и постоянный Docker
volume. `MemoryStorage` в production запрещён, поэтому выбор даты, загрузка документов,
платёжные и консультационные шаги не сбрасываются при перезапуске приложения.

Для одного `BOT_TOKEN` допускается один polling-потребитель. Приложение удерживает
singleton-lock в PostgreSQL или общем backup-volume и сохраняет накопленные updates при
`TELEGRAM_DROP_PENDING_UPDATES=false`.

## Per-document envelope encryption

Новые документы сохраняются в формате `DLCENC2`. Для каждого файла генерируется отдельный
случайный 256-битный data encryption key (DEK). Сам файл содержит только envelope id,
контрольную сумму, nonce и AES-GCM ciphertext. Обёрнутый DEK хранится отдельно в базе
данных и в файл не записывается.

Ротация `DOCUMENT_ENCRYPTION_KEY` переоборачивает DEK в базе без перезаписи ciphertext.
Одинаковые исходные документы получают разные envelope id, ключи, nonce, ciphertext и
случайные имена контейнеров; разные записи больше не используют один физический файл. Если
регистрация загрузки не завершилась или обнаружен дубль, новый контейнер удаляется с
синхронизацией каталога.

Старые plaintext-файлы и контейнеры `DLCENC1` читаются только для контролируемой фоновой
миграции в `DLCENC2`. До завершения миграции выдача такого документа блокируется, а не
откатывается к менее защищённому режиму.

## Контролируемые статусы и воспроизводимый калькулятор

Статусы дела меняются только через единый `CaseService` и явный граф допустимых переходов.
Повтор одного статуса идемпотентен и не создаёт дубли истории или SLA. Принудительное
исправление доступно только административному или системному процессу с обязательным
комментарием и отметкой `forced` в аудите.

Расчёт неустойки выполняется только на явно переданную дату с `Decimal`-арифметикой. В базе
сохраняются дата расчёта, применённая ключевая ставка, коэффициент и версия формулы, поэтому
результат можно воспроизвести после изменения настроек. Значение `LEGAL_KEY_RATE` задаётся
долей: `0.16` означает 16%.

Архитектурные проверки выполняются в CI и development-контейнере. Они блокируют прямую
запись `case.status`, несанкционированный `force=True`, пользовательские `noop`-кнопки и
дублирующиеся HTTP method/path.

## Хранение закрытых дел и legal hold

`/retention/ui` доступен только через персональную MFA-сессию суперадминистратора. Система
сначала находит закрытые дела с истёкшим настраиваемым сроком хранения, затем требует
отдельный запрос и одобрение другим суперадминистратором. `legal hold` блокирует запрос,
одобрение и выполнение удаления.

При атомарном переходе удаления в `EXECUTING` база сначала уничтожает обёрнутые DEK
документов дела. Только после успешного commit процесс приступает к unlink файлов и очистке
содержимого. Поэтому оставшийся после аварии ciphertext не расшифровывается без
уничтоженного ключевого материала, а rollback до commit не оставляет дело в частично
уничтоженном состоянии.

Удаление очищает содержимое дела, сообщения, уведомления, расчёт, зашифрованные файлы и
одноразовые разрешения доступа. Номер дела, tombstone, платёжный ledger и HMAC-защищённая
цепочка аудита сохраняются. Перед файловой операцией все пути проверяются внутри
`STORAGE_DIR`; symlink и path traversal запрещены. Операция идемпотентна и может быть
продолжена после частичного сбоя. Удаление файла выполняется как `unlink` с `fsync` каталога
и не выдаётся за гарантированное физическое перезаписывание SSD/COW-хранилища.

Криптографическое уничтожение применяется к рабочей базе. Старые резервные копии,
созданные до уничтожения DEK, могут сохранять прежний wrapped key до истечения срока их
хранения и должны удаляться по отдельной утверждённой backup-политике.

По умолчанию scheduler работает безопасно:

```text
CLOSED_CASE_RETENTION_DAYS=1825
CASE_RETENTION_SCAN_BATCH_SIZE=100
CASE_RETENTION_EXECUTION_TIMEOUT_SECONDS=900
CASE_RETENTION_DRY_RUN=true
```

Период `1825` дней является конфигурируемой операционной настройкой, а не утверждением об
обязательном юридическом сроке. До отключения dry-run политика должна быть утверждена
юристом и ответственным за защиту данных. Сам scheduler ничего не удаляет: он только
обнаруживает или, при явной настройке, ставит дела в очередь.

## Зашифрованные резервные копии

Создание согласованной копии PostgreSQL/SQLite и хранилища документов выполняется внутри
работающего контейнера:

```bash
bash ./backup.sh
```

Для Timeweb:

```bash
COMPOSE_FILE=docker-compose.timeweb.yml bash ./backup.sh
```

Результат сохраняется только в формате
`backups/legal_concierge_YYYYMMDD_HHMMSS.dlcbak`. Контейнер зашифрован AES-256-GCM и после
создания автоматически проходит полную проверку AEAD и manifest SHA-256.

Проверка существующей копии без Python на хосте:

```bash
docker compose exec -T app python -m app.security.backup_cli verify \
  backups/legal_concierge_YYYYMMDD_HHMMSS.dlcbak
```

Безопасное восстановление выполняется только в пустой staging-каталог:

```bash
bash ./restore.sh /absolute/path/backup.dlcbak /absolute/empty/staging
```

Команда не перезаписывает работающую базу или каталог приложения. После проверки сервис
необходимо остановить и перенести восстановленные данные по утверждённой эксплуатационной
процедуре.

`.env`, токены, криптографические ключи, временные загрузки и quarantine в backup не
включаются. Production-секреты должны храниться отдельно в secret manager и в независимом
защищённом recovery-комплекте.

## Безопасность платёжных webhook

Все входящие события проходят через таблицу `payment_webhook_events`. Один provider event
обрабатывается только один раз; повтор после завершения возвращает идемпотентный ответ и не
создаёт повторных переходов статуса, уведомлений или проводок.

Одинаковый event ID с изменённым телом переводится в `DEAD_LETTER`, возвращает `409` и
регистрируется в Security Center как возможная replay-подмена. Тело запроса ограничивается
до разбора JSON, зависшие события допускают контролируемый retry, а число попыток
ограничено.

В базе сохраняются SHA-256 исходного канонического JSON и минимизированная сводка. Полные
webhook/API payload, реквизиты карты, email клиента и иные лишние персональные данные в
историю дела не записываются. Для YooKassa итоговый статус, сумма, валюта и metadata
дополнительно перепроверяются через API провайдера.

Production-настройки:

```text
PAYMENT_PROVIDER=yookassa
PAYMENT_WEBHOOK_SECRET=<случайный секрет длиной не менее 32 символов>
YOOKASSA_SHOP_ID=<идентификатор магазина>
YOOKASSA_SECRET_KEY=<секретный ключ>
MAX_PAYMENT_WEBHOOK_KB=256
PAYMENT_WEBHOOK_PROCESSING_TIMEOUT_SECONDS=300
PAYMENT_WEBHOOK_MAX_ATTEMPTS=8
```

Production preflight не допускает запуск с `PAYMENT_PROVIDER=fake`.

## Reverse proxy и реальный IP клиента

По умолчанию приложение не доверяет `X-Forwarded-For`, `X-Real-IP` и
`X-Forwarded-Proto`. Это защищает login throttling, Security Center и webhook-аудит от
подмены IP внешним клиентом.

В production укажите только фактические внутренние адреса ingress, nginx, Caddy или load
balancer:

```text
TRUSTED_PROXY_CIDRS=10.10.0.0/16,172.20.0.10/32
TRUSTED_PROXY_MAX_HOPS=5
TRUST_FORWARDED_PROTO=true
```

Не указывайте `0.0.0.0/0` или `::/0`. Цепочка разбирается справа налево до ближайшего
недоверенного адреса. Если CIDR, IP, длина цепочки или forwarded proto некорректны,
приложение использует прямой peer IP, отклоняет заголовок и отражает проблему в `/ready`
или Security Center.

Timeweb compose по умолчанию публикует порт приложения только на `127.0.0.1`, чтобы внешний
доступ проходил через host reverse proxy с TLS.

## Перед реальным запуском

1. Заполните `.env` на основе `.env.production.example`.
2. Укажите реальный PostgreSQL URL, Telegram token, домен, CIDR proxy и YooKassa.
3. Выполните `bash ./timeweb-deploy.sh` — миграции, bootstrap и startup-backup выполняются
   внутри контейнера автоматически.
4. Убедитесь, что `COMPOSE_FILE=docker-compose.timeweb.yml bash ./status.sh` показывает
   healthy Redis, healthy app и `/ready` с `ok=true`.
5. До переключения клиентов проверьте `/start`, калькулятор, загрузку документов, оплату,
   запись на консультацию, кабинет юриста и административный контур.

Старые plaintext `.tar.gz/.zip/.db` не допускаются к восстановлению новым механизмом.

## Planned integration roadmap

Canonical implementation plan:

- [docs/DIGITAL_LEGAL_CONCIERGE_INTEGRATION_MASTER_PLAN_2026-10-01.md](./docs/DIGITAL_LEGAL_CONCIERGE_INTEGRATION_MASTER_PLAN_2026-10-01.md)

This document is a planned implementation source. Future full-roadmap work should cite this filename explicitly and follow its phases, authority boundaries, dependencies and acceptance gates.
