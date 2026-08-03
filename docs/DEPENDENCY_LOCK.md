# Воспроизводимые зависимости релиза

`constraints.txt` фиксирует полный Python 3.11/Linux dependency set, на котором прошли:

- полный pytest-набор;
- SQLite migrations и schema check;
- production image startup;
- PostgreSQL migrations, encrypted backup и staging restore;
- Redis FSM reconnect integration;
- Docker/Timeweb deployment contracts.

Production и test Dockerfiles задают `PIP_CONSTRAINT=/app/constraints.txt`, поэтому обычный
`pip install .` и `pip install .[test]` не могут незаметно выбрать более новые версии.

## Проверка

```bash
bash ./test.sh tests/test_dependency_lock_contract.py
```

Полная image-проверка выполняется workflow `Reproducible Dependencies`:

1. собирается test-image;
2. `verify_constraints.py --require-all` сверяет все установленные версии;
3. выполняются `pip check` и полный pytest;
4. собирается production-image;
5. проверяются production direct dependencies, доступные constraints и `pip check`.

## Обновление

Dependency set меняется только отдельным pull request:

1. обновить нужные версии в `constraints.txt`;
2. проверить совместимость с диапазонами `pyproject.toml`;
3. дождаться всех CI, deployment, Redis, PostgreSQL recovery и reproducibility gates;
4. не смешивать dependency refresh с функциональными изменениями без необходимости.

Lock не содержит секретов и пользовательских данных. Он рассчитан на Python 3.11 и Linux,
то есть на тот же runtime, который используется в Docker/Timeweb deployment.
