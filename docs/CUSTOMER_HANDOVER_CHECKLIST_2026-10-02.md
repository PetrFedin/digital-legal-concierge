# DIGITAL LEGAL CONCIERGE — ЧЕК-ЛИСТ ПЕРЕДАЧИ ЗАКАЗЧИКУ

**Дата фиксации:** 02.10.2026  
**Назначение:** единый контроль завершения проекта без подмены фактической приемки наличием кода.

Статусы:

- **DONE_SOURCE** — функциональность/архитектура реализована и зафиксирована в репозитории.
- **AUTOMATED_PROOF** — соответствующий изолированный runtime/CI-контур фактически выполнялся успешно на текущем стеке; после изменения SHA доказательство повторяется.
- **LIVE_REQUIRED** — нужна фактическая staging/UAT/pilot проверка на замороженном SHA.
- **EXTERNAL_INPUT** — требуются внешние учетные данные, инфраструктура или реальные участники; их нельзя выдумывать.

| № | Область | Текущее состояние | Что является финальным доказательством |
|---|---|---|---|
| 1 | Единая спецификация и технические дубли | **DONE_SOURCE** | `CANONICAL_PRODUCT_SPEC_2026-10-02.md`, `AUTHORITY_MANIFEST.yml`, новый Docker-first `START_HERE.md`; исторические versioned-документы понижены до evidence/context. |
| 2 | Backend/DB архитектура | **DONE_SOURCE** | `TECHNICAL_ARCHITECTURE_CURRENT.md` + `SYSTEM_CONTRACT_CURRENT.md`; PostgreSQL/Redis/ClamAV/Docker и authority boundaries зафиксированы. |
| 3 | БД и State Machine | **DONE_SOURCE + AUTOMATED_PROOF` на предшествующем exact stack** | Alembic head, ORM parity, PostgreSQL migration/concurrency, `case_transition_policy.py`, `CaseService`; повторить exact-SHA gate на release candidate. |
| 4 | Backend бизнес-логики | **DONE_SOURCE** | М1/М2, payment, consultation, documents, notifications, retention, audit/domain services; финальный full release gate на frozen SHA. |
| 5 | Telegram-бот | **DONE_SOURCE + AUTOMATED_PROOF` на предшествующем exact stack** | Redis/Telegram Runtime Contracts + реальные M1/M2 Telegram persona tests. |
| 6 | CRM администратора | **DONE_SOURCE + AUTOMATED_PROOF` на предшествующем exact stack** | Browser Staff E2E + staging/UAT администратора. |
| 7 | Рабочее место юриста | **DONE_SOURCE + AUTOMATED_PROOF` на предшествующем exact stack** | Browser Staff E2E + UAT ответственного/чужого юриста. |
| 8 | Документы и storage | **DONE_SOURCE + AUTOMATED_PROOF`** | ClamAV admission, encrypted source, portable Case keys, one-time grants, pikepdf/OCR derivatives, key rotation/retention/restore. |
| 9 | Платежи и webhook | **DONE_SOURCE; LIVE_REQUIRED / EXTERNAL_INPUT для реального provider** | Offline reconciliation может быть production-механизмом. YooKassa требует реальные test-shop credentials и provider callback evidence; до этого provider live PASS не заявляется. |
| 10 | Календарь консультаций | **DONE_SOURCE + AUTOMATED_PROOF`** | slot reservation/race/expiry/reschedule/no-show/payment consistency на PostgreSQL + M2 UAT. |
| 11 | Уведомления и retry | **DONE_SOURCE** | durable Notification, dispatcher, scheduler reminders/retries; staging delivery + backlog/retry check. |
| 12 | Роли и права | **DONE_SOURCE + AUTOMATED_PROOF`** | server-side RBAC, responsibility checks, MFA/session controls, Browser Staff E2E; UAT cross-role denial. |
| 13 | Логи, аудит, backup, мониторинг | **DONE_SOURCE; LIVE_REQUIRED для operational ownership** | tamper-evident audit, encrypted authenticated backup/restore, readiness/health, scheduler/security events; staging alert destination/ownership и restore drill. |
| 14 | Staging | **LIVE_REQUIRED / EXTERNAL_INPUT** | изолированные PostgreSQL + Redis + storage + ClamAV + Telegram acceptance bot. На доступном Render free-контуре отдельные stateful slots сейчас заняты другими проектами; общую БД использовать нельзя. |
| 15 | Полное тестирование М1/М2 | **AUTOMATED частично; LIVE_REQUIRED** | текущие focused/PostgreSQL/Redis/browser gates + реальные M1 full representation, M1 self-filing, M2 personas на одном frozen SHA. |
| 16 | Ошибочные/нестандартные сценарии | **DONE_SOURCE / AUTOMATED частично** | stale/duplicate/race/retry/timeout/no-show/refund/storage/security tests; complete exact-SHA differential без candidate-owned regression. |
| 17 | Пользовательская приемка | **LIVE_REQUIRED** | утвержденный UAT протокол: клиент + администратор + юрист + руководитель, реальные сценарии и замечания. |
| 18 | Ограниченный пилот | **LIVE_REQUIRED / EXTERNAL_INPUT** | ограниченное число реальных обращений под наблюдением, без расширения аудитории до PASS. |
| 19 | Исправления пилота | **после 18** | каждое замечание → issue → fix → regression → повтор acceptance; новая версия SHA заново проходит release chain. |
| 20 | Полноценная эксплуатация | **после 1–19** | production deployment, monitoring ownership, backup/restore PASS, payment mechanism PASS, pilot sign-off, release tag/SHA. |

## Что не является блокером разработки

Не нужно заново писать State Machine, CRM, lawyer workspace, платежный lifecycle, calendar или Telegram-навигацию: эти authorities уже существуют. На финише приоритет — не еще один параллельный слой, а один release candidate, устранение candidate-owned регрессий и реальная приемка.

## Что нельзя честно закрыть без внешних данных

1. Реальный YooKassa webhook/paid/refund — без test-shop credentials.
2. Реальная Telegram UAT — без acceptance bot/token и нескольких реальных client/staff identities.
3. Реальная SMTP-доставка self-filing — без acceptance SMTP/mailbox credentials.
4. Staging на отдельной инфраструктуре — без изолированных stateful ресурсов.
5. Pilot/production — без реальных обращений, операционных владельцев и решения заказчика о запуске.

Эти пункты не заменяются mock/fake evidence.

## Ближайшая последовательность

`release source freeze → exact CI/differential → staging → real Telegram M1/M2/UAT → payment/mail evidence → backup→restore → pilot → fixes → production`.
