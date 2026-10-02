# START HERE — Digital Legal Concierge

**Release mode:** customer handover / frozen MVP M1+M2.

Перед запуском прочитайте:

1. `docs/CANONICAL_MVP_SPEC_2026-10-03.md`;
2. `docs/FINAL_ARCHITECTURE_2026-10-03.md`;
3. `docs/FINAL_CUSTOMER_HANDOVER_CHECKLIST_2026-10-03.md`.

## Локальный запуск

На хосте нужны Docker Engine/Desktop и Docker Compose plugin.

```bash
cp .env.example .env
bash ./run.sh
```

Проверка:

```bash
bash ./status.sh
bash ./acceptance.sh
```

Основные страницы:

- `/maintenance-center/ui` — единая эксплуатационная точка входа;
- `/operator` — staff workspace;
- `/admin-ui` — администратор;
- `/lawyer/ui` — юрист;
- `/ready` — readiness.

## Production-like / staging

```bash
cp .env.production.example .env
bash ./generate-secrets.sh
# заполните реальные external credentials в .env
bash ./timeweb-deploy.sh
COMPOSE_FILE=docker-compose.timeweb.yml bash ./status.sh
bash ./acceptance.sh
```

Production contour требует PostgreSQL, Redis FSM, persistent encrypted storage, HTTPS, реальный BOT_TOKEN и реальный payment provider configuration.

## Перед передачей заказчику

Не добавляйте новые модули. Закрываются только:

- CI/release defects;
- staging;
- real Telegram smoke;
- real payment/webhook smoke;
- UAT М1/М2;
- pilot 3–5 обращений;
- подтверждённые замечания пилота.

Integration Master Plan и иные исследовательские улучшения являются post-MVP backlog и не блокируют текущую передачу.
