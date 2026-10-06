# Digital Legal Concierge — финальный чек-лист передачи заказчику

**Исходная дата:** 2026-10-03  
**Acceptance update:** 2026-10-06  
**Release authority:** полный accepted Git SHA на `main`, зеркально зафиксированный в `release/customer-handover-20261005`  
**Scope:** только М1/М2. Никакого расширения MVP до закрытия передачи.

Статусы:

- **DONE** — код/документ реализован в release candidate;
- **CI_GATE** — требуется зелёное доказательство exact candidate;
- **LIVE_REQUIRED** — нельзя честно закрыть без внешней среды/credentials/реального пользователя или provider;
- **PILOT_REQUIRED** — выполняется после staging/UAT.

| # | Пункт | Статус | Критерий закрытия |
|---:|---|---|---|
| 1 | Заморозить единую спецификацию | DONE | `CANONICAL_MVP_SPEC_2026-10-03.md`; старые versioned docs объявлены историческими |
| 2 | Зафиксировать backend/DB архитектуру | DONE | `FINAL_ARCHITECTURE_2026-10-03.md` |
| 3 | БД и State Machine | DONE / CI_GATE | CaseService + transition policy + Alembic + PostgreSQL target; exact migration gates должны быть green |
| 4 | Backend бизнес-логики | DONE / CI_GATE | М1/М2 domain services и guarded transitions; полный suite green |
| 5 | Telegram-бот | DONE / LIVE_REQUIRED | Код готов; нужен real Bot API smoke на exact release |
| 6 | CRM администратора | DONE / CI_GATE | Admin/operator workspaces + protected actions; browser E2E green |
| 7 | Рабочее место юриста | DONE / CI_GATE | Lawyer workspace/review/consultation flows; browser E2E green |
| 8 | Документы и хранилище | DONE / LIVE_REQUIRED | encrypted persistent storage реализован; на staging проверить upload/read/restart/backup |
| 9 | Платежи и реальные webhook | DONE / LIVE_REQUIRED | YooKassa adapter/ledger реализованы; нужен controlled real provider payment + webhook proof |
| 10 | Календарь консультаций | DONE / CI_GATE | internal slots/hold/book/reschedule/cancel/outcome; concurrency tests green |
| 11 | Уведомления и повторные отправки | DONE / CI_GATE | outbox/retry/backoff/reminders; delivery tests green |
| 12 | Роли и права | DONE / CI_GATE | role-aware admin/lawyer access, sessions/MFA, exact authorization tests green |
| 13 | Логи, аудит, backup, monitoring | DONE / LIVE_REQUIRED | код реализован; staging backup/restore + readiness evidence required |
| 14 | Staging | LIVE_REQUIRED | production-like PostgreSQL + Redis + storage + HTTPS + real test bot/provider |
| 15 | Полностью протестировать М1/М2 | CI_GATE | exact candidate full automated gates green + staging end-to-end M1/M2 |
| 16 | Ошибочные/нестандартные сценарии | CI_GATE | full suite, stale/replay/retry/concurrency/permission/error recovery gates green |
| 17 | Пользовательская приёмка | LIVE_REQUIRED | заказчик/назначенный UAT участник подписывает M1/M2 acceptance |
| 18 | Ограниченный пилот | PILOT_REQUIRED | 3–5 реальных/контролируемых обращений без критических дефектов |
| 19 | Исправить замечания пилота | PILOT_REQUIRED | только подтверждённые defects/UX blockers; scope expansion отдельным CR |
| 20 | Полноценная эксплуатация | LIVE_REQUIRED | production deploy exact accepted artefact, backup/restore evidence, support owner назначен |

## Release gates перед staging

Exact commit обязан пройти. Для staging/production оператор задаёт полный
`DEPLOY_EXACT_SHA`; acceptance должна доказать совпадение checkout, OCI image revision
и `/runtime/release` с этим SHA.

1. Python compile;
2. architecture check;
3. Alembic fresh migration;
4. Alembic current/idempotency;
5. ORM/schema parity;
6. полный SQLite suite;
7. PostgreSQL migration + backup/restore drill;
8. container build/start;
9. locked dependency suite;
10. Telegram runtime contracts;
11. browser staff E2E;
12. PostgreSQL concurrency.

## Staging smoke

До функционального smoke staging обязан быть изолирован от production отдельными
Compose project/volumes, Telegram bot token, YooKassa test shop и HTTPS endpoint.
`/health`, `/ready` и `/runtime/release` должны пройти exact-SHA acceptance.

### Клиент

- /start;
- calculator;
- сохранить/возобновить незавершённый шаг;
- М1: consent → документы → lawyer review → договор → платёж;
- М2: описание → документы/skip → слот → оплата → booked → result;
- «Моё дело»;
- сообщения;
- stale button recovery;
- restart во время FSM шага.

### Администратор

- login/MFA;
- очередь дел;
- назначение юриста;
- payment exception/review;
- consultation control;
- audit/security/backup pages;
- forbidden actions действительно запрещены.

### Юрист

- назначенные дела;
- document review;
- запрос новой версии;
- accept/transfer M1↔M2 по разрешённому графу;
- consultation preparation/result;
- case history/messages.

### External

- Telegram API;
- HTTPS;
- YooKassa create payment;
- webhook;
- webhook replay;
- backup;
- restore в staging.

## Pilot exit criteria

Пилот считается закрытым, если:

- нет P0/P1 дефектов;
- нет потери документов/платежей/истории;
- нет недопустимого перехода State Machine;
- ни один provider event не обработан дважды;
- backup восстановлен;
- пользователь всегда видит понятный следующий шаг;
- замечания пилота классифицированы как defect / UX / post-MVP request.

## Production handover package

Заказчику передаются:

- репозиторий и exact release SHA;
- canonical specification;
- architecture;
- production env template без секретов;
- deploy/runbook;
- acceptance evidence;
- backup/restore инструкция;
- admin/lawyer operating instructions;
- список внешних credentials и владельцев;
- open post-MVP backlog отдельно от принятого MVP.

**Запрещено маркировать проект PRODUCTION ACCEPTED, пока LIVE_REQUIRED пункты не подтверждены реальной средой.**
