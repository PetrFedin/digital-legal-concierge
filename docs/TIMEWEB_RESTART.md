# Перезапуск Digital Legal Concierge на Timeweb через Docker

## Production-схема

Для текущей версии используйте облачный сервер Timeweb с Docker Compose и постоянными
Docker volumes. Базу данных размещайте в PostgreSQL; предпочтительно — в управляемом
PostgreSQL Timeweb в той же приватной сети. Redis запускается рядом с приложением без
публикации порта и хранит незавершённые Telegram-сценарии в постоянном AOF-volume.

App Platform через Docker Compose не подходит для текущего файлового контура без переноса
документов и backup в S3: App Platform создаёт новое окружение при деплое и запрещает
директиву `volumes`, а приложению нужны постоянные `/app/storage`, `/app/backups` и Redis
FSM data.

Официальная документация:

- https://timeweb.cloud/docs/apps/deploying-with-docker-compose
- https://timeweb.cloud/docs/apps/how-it-works
- https://timeweb.cloud/docs/cloud-servers
- https://timeweb.cloud/docs/dbaas/postgresql/connect-to-database

## Что требуется на сервере

Только:

- Git;
- Docker Engine;
- Docker Compose plugin.

Python, pip, Redis и virtualenv отдельно на сервере не нужны. Python, Redis и зависимости
запускаются контейнерами Docker.

## Первый запуск

```bash
git clone https://github.com/PetrFedin/digital-legal-concierge.git
cd digital-legal-concierge
cp .env.production.example .env
bash ./generate-secrets.sh
```

Перенесите значения из `.env.generated.secrets` в `.env`, заполните `BOT_TOKEN`,
`DATABASE_URL`, домен, YooKassa и фактические CIDR reverse proxy. Не меняйте
`FSM_STORAGE_BACKEND=redis` и `REDIS_URL=redis://redis:6379/0` для штатного Compose.
Затем удалите файл с сгенерированными секретами.

```bash
rm -f .env.generated.secrets
bash ./test.sh
bash ./timeweb-deploy.sh
```

## Что выполняется автоматически

1. Проверяется, что deploy выполняется из чистого commit ветки `main`.
2. Image получает immutable tag из первых 12 символов Git SHA и OCI release labels.
3. Проверяются production-переменные без вывода секретов.
4. Запускается Redis с AOF и ожидается его healthcheck.
5. Проверяется доступность Redis из контейнера приложения.
6. Ожидается PostgreSQL и выполняется `SELECT 1` с ограниченным retry.
7. Применяются Alembic-миграции.
8. Создаётся системный администратор, если он отсутствует.
9. Демо-юрист не создаётся при `BOOTSTRAP_DEMO_DATA=false`.
10. Создаётся и полностью проверяется encrypted startup-backup, если свежего backup нет.
11. Запускаются API, Telegram polling и scheduler единым supervisor-процессом.
12. Проверяются Redis, `/health`, fail-closed `/ready` и `/runtime/release`.
13. Release state обновляется только после совпадения работающего commit с OCI image label.

## Повторный деплой

```bash
cd digital-legal-concierge
git pull --ff-only
bash ./test.sh
bash ./timeweb-deploy.sh
```

Перед заменой контейнера deploy-скрипт создаёт encrypted backup. Если production preflight
не пройден, работающая версия не заменяется. Состояния FSM остаются в `concierge_redis`,
поэтому незавершённые диалоги не сбрасываются при обновлении приложения.

Новый image хранится как `<repository>:<12-char-git-sha>`. Предыдущий successful release
остаётся локальным rollback-кандидатом. `.release/current.env` и
`.release/previous.env` не содержат секретов, не коммитятся и не попадают в Docker context.

## Проверка работающего release

```bash
COMPOSE_FILE=docker-compose.timeweb.yml bash ./status.sh
```

Команда проверяет Redis, `/health`, `/ready`, `/runtime/release` и совпадение полного Git
commit с `org.opencontainers.image.revision` реально запущенного контейнера.

## Schema-safe rollback

```bash
COMPOSE_FILE=docker-compose.timeweb.yml bash ./rollback.sh
```

Rollback разрешён только при одинаковой Alembic-head current и previous image. Перед
переключением создаётся новый encrypted backup. Previous image запускается без rebuild и
должен подтвердить свой commit и readiness. Если он не проходит проверки, исходный current
image возвращается автоматически.

При изменённой или неизвестной Alembic-head автоматический rollback блокируется. База данных
не понижается автоматически: используйте backup и проверенное staging restore. Подробно:
[`RELEASE_ROLLBACK.md`](RELEASE_ROLLBACK.md).

## Операционные команды

```bash
COMPOSE_FILE=docker-compose.timeweb.yml bash ./status.sh
COMPOSE_FILE=docker-compose.timeweb.yml bash ./backup.sh
COMPOSE_FILE=docker-compose.timeweb.yml bash ./rollback.sh
COMPOSE_FILE=docker-compose.timeweb.yml bash ./bot-control.sh
COMPOSE_FILE=docker-compose.timeweb.yml docker compose logs -f --tail=300 app redis
bash ./test.sh
```

Точечный запуск теста также не требует Python на сервере:

```bash
bash ./test.sh tests/test_release_identity_rollback_contract.py
```

## Ограничения и правила

- Для одного `BOT_TOKEN` должен работать только один polling-потребитель. Приложение
  удерживает singleton-lock в PostgreSQL или общем backup-volume.
- `TELEGRAM_DROP_PENDING_UPDATES=false` сохраняет накопленные Telegram updates при
  перезапуске.
- Redis FSM обеспечивает продолжение незавершённого диалога, но не заменяет PostgreSQL как
  юридически значимый источник данных.
- Не публикуйте порт Redis наружу и не добавляйте `ports` к сервису `redis`.
- Timeweb compose публикует API только на `127.0.0.1:8000`; внешний TLS-доступ должен идти
  через host reverse proxy. Укажите его фактический адрес в `TRUSTED_PROXY_CIDRS`.
- Не обходите migration-head guard для rollback старого image.
- `CASE_RETENTION_DRY_RUN=true` оставляйте включённым до отдельного юридического
  утверждения политики удаления.
- Старые ключи переносите в соответствующие `*_PREVIOUS_KEYS`; не удаляйте их до
  завершения миграции документов и срока хранения backup.
- Не коммитьте `.env`, `.env.generated.secrets`, `.release`, ключи, дампы и документы.
- Перед переходом со старой базы обязательно создайте отдельную проверенную копию и
  выполните тестовое восстановление в staging.
