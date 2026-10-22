# Immutable release и безопасный rollback

## Идентификация работающей версии

Каждый production image собирается с immutable tag из первых 12 символов полного Git SHA и
OCI-метками:

- `org.opencontainers.image.version`;
- `org.opencontainers.image.revision`;
- `org.opencontainers.image.created`.

Работающий контейнер публикует несекретный endpoint:

```text
GET /runtime/release
```

Он возвращает application version, release, полный commit, build timestamp, image repository,
image tag и ожидаемые Alembic heads. `status.sh` сравнивает commit endpoint с OCI revision
реально запущенного контейнера и завершается ошибкой при расхождении.

## Деплой

`deploy.sh`:

1. требует Git checkout без изменений tracked-файлов, **полный `DEPLOY_EXACT_SHA` из 40 символов** и совпадение его с текущим commit;
2. по умолчанию разрешает запуск только из `main`; изолированный Draft/staging может использовать `ALLOW_NON_MAIN_DEPLOY=true` **только** по явной инструкции оператора;
3. собирает image `<repository>:<12-char-sha>`;
4. однозначно определяет `DEPLOY_TARGET`: `docker-compose.yml` по умолчанию только `local`, `docker-compose.production.yml` и `docker-compose.timeweb.yml` по умолчанию **`production`** (не `local`);
5. запускает **предварительную проверку допуска внутри candidate image** через `scripts/deploy_admission.py`, до резервного копирования и перезапуска;
6. для production требует и `DEPLOY_TARGET=production`, и `APP_ENV=production`, и внешний подтверждённый допуск `status=ACCEPTED`, `customer_accepted=True`, `release_authorized=True`; сейчас он отсутствует, поэтому production блокируется;
7. для явного staging допускает только `DEPLOY_TARGET=staging` с `APP_ENV=test/staging` и **отдельным `COMPOSE_PROJECT_NAME=dlc-staging-<id>`** на Timeweb/production-compose; для локального запуска — только `DEPLOY_TARGET=local` с `APP_ENV=local/development/test`;
8. проверяет OCI revision и Alembic head нового image, выполняет конфигурационный production preflight;
9. создаёт encrypted backup ранее работающей версии;
10. только после допусков запускает новый image и проверяет Redis, `/health`, `/ready`, `/runtime/release`;
11. только после успеха записывает `.release/current.env` и `.release/previous.env`.

Для проверки на **изолированном staging** оператор сначала утверждает раздельные тестовые секреты и хранилища, затем использует явное назначение, например:

```bash
COMPOSE_FILE=docker-compose.timeweb.yml \
COMPOSE_PROJECT_NAME=dlc-staging-m1m2 \
DEPLOY_TARGET=staging \
ALLOW_NON_MAIN_DEPLOY=true \
DEPLOY_EXACT_SHA="$(git rev-parse --verify HEAD)" \
bash ./deploy.sh
```

Этот пример — инструкция, **не подтверждение**, что Timeweb подключён или тестовый запуск выполнен. Для production нельзя подменять `APP_ENV`, `DEPLOY_TARGET` или учётные данные staging ради обхода запрета.

**Текущий релиз — HOLD.** Наличие `DEPLOY_EXACT_SHA`, `/ready=200`, настроенного YooKassa или локального score 100% не означает юридическое согласование, независимое ревью и подписанную приёмку М1/М2. Не изменяйте `APP_ENV` ради обхода production-блокировки. Для staging используйте отдельные PostgreSQL, Redis, зашифрованное файловое хранилище, тестовый Telegram и sandbox-платежи, только с разрешения инфраструктурного владельца.

Release state не содержит секретов, исключён из Git и Docker build context, имеет права
`0600`. Старый image не пересобирается и остаётся локальным rollback-кандидатом.

## Автоматический rollback при неуспешном deploy

Автоматический возврат предыдущего image выполняется только когда:

- предыдущий release state существует;
- предыдущий immutable image доступен локально;
- Alembic head предыдущего и нового image совпадает.

Это предотвращает запуск старого кода на неизвестной или новой схеме БД. При несовпадении
схем deploy завершается ошибкой и направляет к восстановлению backup в staging.

## Ручной rollback

```bash
COMPOSE_FILE=docker-compose.timeweb.yml bash ./rollback.sh
```

Скрипт:

1. сравнивает migration heads current/previous;
2. создаёт новый encrypted backup текущего состояния;
3. запускает previous image с `--no-build`;
4. проверяет release commit и `/ready`;
5. меняет current/previous state местами только после успеха.

Если previous image не проходит проверки, скрипт возвращает исходный current image. База
данных автоматически не восстанавливается и не понижается.

## Когда rollback image запрещён

При изменённой Alembic head нельзя обходить блокировку. Используйте:

1. backup перед deploy;
2. `restore.sh` в пустой staging-каталог/БД;
3. проверку восстановленной версии;
4. утверждённую аварийную процедуру переключения.

Rollback кода и восстановление данных — разные операции. Наличие старого Docker image не
означает совместимость со схемой или данными новой версии.
