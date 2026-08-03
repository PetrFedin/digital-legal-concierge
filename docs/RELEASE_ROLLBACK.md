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

1. требует Git checkout без изменений tracked-файлов;
2. по умолчанию разрешает production deploy только из `main`;
3. собирает image `<repository>:<12-char-sha>`;
4. проверяет OCI revision;
5. определяет Alembic head нового image;
6. выполняет production preflight;
7. создаёт encrypted backup работающей версии;
8. запускает новый image;
9. проверяет Redis, `/health`, `/ready` и `/runtime/release`;
10. только после успеха записывает `.release/current.env` и `.release/previous.env`.

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
