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

1. Проверка production-переменных без вывода секретов.
2. Запуск Redis с AOF и ожидание его healthcheck.
3. Проверка доступности Redis из контейнера приложения.
4. Ожидание PostgreSQL и проверка `SELECT 1` с ограниченным retry.
5. Alembic-миграции базы.
6. Создание системного администратора, если он отсутствует.
7. Демо-юрист не создаётся при `BOOTSTRAP_DEMO_DATA=false`.
8. Создание и полная проверка зашифрованного startup-backup, если свежего backup нет.
9. Запуск API, Telegram polling и scheduler единым supervisor-процессом.
10. Проверка Redis, `/health` и fail-closed `/ready` после запуска.

## Повторный деплой

```bash
cd digital-legal-concierge
git pull --ff-only
bash ./test.sh
bash ./timeweb-deploy.sh
```

Перед заменой работающего контейнера deploy-скрипт создаёт зашифрованный backup. Если
production preflight не пройден, работающая версия не заменяется. Состояния FSM остаются в
`concierge_redis`, поэтому незавершённые диалоги не сбрасываются при обновлении приложения.

## Операционные команды

```bash
COMPOSE_FILE=docker-compose.timeweb.yml bash ./status.sh
COMPOSE_FILE=docker-compose.timeweb.yml bash ./backup.sh
COMPOSE_FILE=docker-compose.timeweb.yml bash ./bot-control.sh
COMPOSE_FILE=docker-compose.timeweb.yml docker compose logs -f --tail=300 app redis
bash ./test.sh
```

Точечный запуск теста также не требует Python на сервере:

```bash
bash ./test.sh tests/test_docker_only_deployment_contract.py
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
- `CASE_RETENTION_DRY_RUN=true` оставляйте включённым до отдельного юридического
  утверждения политики удаления.
- Старые ключи переносите в соответствующие `*_PREVIOUS_KEYS`; не удаляйте их до
  завершения миграции документов и срока хранения backup.
- Не коммитьте `.env`, `.env.generated.secrets`, ключи, дампы и пользовательские документы.
- Перед переходом со старой базы обязательно создайте отдельную проверенную копию и
  выполните тестовое восстановление в staging.
