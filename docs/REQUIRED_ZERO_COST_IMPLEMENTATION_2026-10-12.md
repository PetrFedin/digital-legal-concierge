# digital-legal-concierge — обязательные доработки / zero-new-cost execution

Snapshot: 2026-10-12. State: REQUIRED, not proof of implementation.

Canon: `README.md`, PR #188, existing security/backup runbooks.

| ID | P | Требуемое действие | Сквозная связь | Definition of Done |
|---|---|---|---|---|
| LEG-ZC-01 | P0 | Зафиксировать договорные и юридические вычисления | input dates → exact formulas → cases → evidence | early handover=0 delay, граничные даты/timezones, unit tests |
| LEG-ZC-02 | P0 | Security and RBAC hardening | accounts → documents → lawyer role → audit access | negative role/tenant tests, immutable audit, no public personal docs |
| LEG-ZC-03 | P0 | Проверить delivery/restart/restore без реальных платежей | existing Timeweb environment → data store → backup → recovery | isolated restoration, exact SHA, health/ready, 0 ₽ incremental verified |
| LEG-ZC-04 | P1 | Проверить всю последовательность клиента и юриста | intake → issue → calculation → document → review → client response | browser mobile/desktop tests, sourced legal templates, explicit disclaimers |

## Обязательный нулевой бюджет и безопасная передача
- Дополнительная стоимость внедрения/эксплуатации нового контура: **0 ₽**. Запрещено включать биллинг, pay-as-you-go, платные CI minutes/API/AI/БД/storage, платные публикации, платные провайдеры, приобретать серверы/домены или выполнять реальные платежи. Уже существующая инфраструктура не доказывает нулевой совокупной стоимости; фиксировать только **incremental cost**.
- Только локальные/open-source инструменты и **доказанные** свободные остатки бесплатных тарифов. Если бесплатные лимиты/учётная запись не проверены: `BLOCKED_NO_FREE_CAPACITY`.
- Не заменять production demo-приложением; не подключать чужие БД; не записывать секреты в GitHub; не обходить CI, review, migrations и RBAC.
- Сначала проверить latest main/PR/CI, затем взять первое доступное P0. Для каждой задачи: implementation files, exact SHA, passing unit+negative+browser/device tests, persistence/restore evidence где применимо, live deployed SHA где нужен, ledger стоимости, статус `PLANNED/IN_CODE/CI_PASS/LIVE_VERIFIED/BLOCKED`.
- Этот файл — **дополнение** к существующему authoritative technical specification / AGENTS.md, а не замена. Не утверждать внедрение на основании создания документа.

Следующий агент: продолжай с первого открытого P0, **делай код и тесты**, не создавай одни планы. В конце обнови issue/PR с evidence и источник истины проекта; merge — только по существующим политикам.
