# Digital Legal Concierge — полный реестр недоделок, приёмки и развития

**Дата инвентаризации:** 15.10.2026. **Decision:** PRODUCTION HOLD / customer acceptance NOT SIGNED.
**Объект:** Digital Legal Concierge, Telegram MVP по ДДУ 214-ФЗ, только маршруты **М1 и М2**.
**Текущий merged main:** `0777c150be8768e468cea85bc5ff5b682c5bdab5` (#186).
**Последний доказанный source-green baseline:** Draft #209, `4364fa80cdfaf0abb428ef76121e60245e2f6763`; 4/4 CI SUCCESS, SQLite+locked по **908 PASS / 8 SKIP**, изолированный PostgreSQL/restore. **НЕ в main, НЕ staging/production UAT.**
**Новая доработка:** Draft #211, начальная ветка от #209; проверка нового SHA проводится отдельно. Ни одна проверка предыдущего SHA не доказывает качество новых изменений.
**Правило затрат:** никаких новых платных провайдеров/инстансов без подтверждённого согласия владельца.

## 1. Иерархия источников и смысл статусов

1. Утверждённое функциональное ТЗ: `source_documents/1 Функциональная_спецификация_MVP_финальная_версия.docx`, этапы 1–19.
2. Утверждённое UX/UI-ТЗ: `source_documents/1 UX_UI_спецификация_Telegram_бот_финальная_версия.docx`, этапы 1–20, B-001…B-028, A-/L-/N-/UX-каталоги.
3. Подтверждённые более поздние требования пользователя, регистрируемые отдельными PR: PM-027 самоподача; PM-016 юридические правила; PM-028 новый старт/preview.
4. `docs/CANONICAL_MVP_SPEC_2026-10-03.md` и `docs/MVP_OPERATIONAL_ACCEPTANCE_EVIDENCE_2026-10-12.md` — действующая трассировка и UAT-01…UAT-16.
5. `docs/DIGITAL_LEGAL_CONCIERGE_INTEGRATION_MASTER_PLAN_2026-10-01.md` — **идеи/план интеграций и стратегического расширения**, а не подтверждение принятой MVP-функциональности.

**CODE_PRESENT** означает наличие исходников, **DRAFT_TESTED** — проверку конкретного unmerged SHA, **STAGING_REQUIRED** — отсутствие реального внешнего доказательства, **LEGAL_BLOCKED** — отсутствие подписи и утверждённой публикации, **DESIGN_ONLY** — идея или проектный контур без принятой рабочей реализации. Ни один из этих терминов не равен **CUSTOMER_ACCEPTED**.

## 2. P0 — обязательные критерии перед допуском клиентов

| ID | Требование и первоисточник | Подтверждённая стадия | Незакрытая работа | Критерий приёмки / владелец |
|---|---|---|---|---|
| DLC-P0-01 | М1: расчёт → B-007 → документы → юрист → договор → 30k → доверенность → претензия → 30 дней → суд → 70k → исполнение → 10% → закрытие (ФТЗ §4, §8, §15–17) | CODE_PRESENT main #186 | Реальная последовательность без обхода State Machine, webhook/ledger, отрицательные переходы | UAT-04…08, юрист+заказчик+провайдер, реальная DB/Telegram |
| DLC-P0-02 | М2: описание → опциональные документы → слот → подтверждённая оплата → консультация → М1 либо закрытие (ФТЗ §5) | CODE_PRESENT main #186 | 2 клиента / один слот, истёкшая бронь, повторы webhook, перенос/отмена | UAT-09/10, юрист+заказчик+провайдер |
| DLC-P0-03 | Ранний/своевременный ввод объекта: 0 просрочки и 0 ₽ (ФТЗ §9) | DRAFT_TESTED #188 | Review + юридическая проверка расчётных примеров, реальный бот | UAT-02, юрист по правилам + владелец |
| DLC-P0-04 | Повторный расчёт по другому объекту без смешивания дел (ФТЗ §3.4, §12.5, §18.14) | DRAFT_TESTED #189/#191–#193 | Safe merge, реальные старые/double-tap callback, многоделовая история | UAT-01/03/11, 2 разных клиента, оператор |
| DLC-P0-05 | Закрытый архив без новых записей, много дел и безопасная пагинация (ФТЗ §12/§14/§18; UX17-ARCHIVE-001) | DRAFT_TESTED #195/#197/#204–#207 | Разрешённый read-only доступ vs одобренное удаление; старые callback; проверить новый #211 | UAT-11/15, security+UX reviewer |
| DLC-P0-06 | **Сразу** закрыть клиентское чтение после APPROVED/EXECUTING/FAILED/COMPLETED, до физического удаления | Исправление в Draft #211, CI отдельно | Source query + реальный PostgreSQL, старые Telegram-кнопки и гонки | Во всех client/staff views нет частных данных; security owner |
| DLC-P0-07 | Защищённые исходные архивные PDF доступны **самому клиенту**, а не только сотруднику (ФТЗ §10; UX10/UX17) | DRAFT preflight+grant #200–#202; #196 OPEN | Одноразовая доставка реальных байтов; аутентификация; согласованный канал; no-replay; подтверждённая доставка | E2E файл на тестового клиента, owner+legal+security |
| DLC-P0-08 | ПД: consent B-007, точный оператор, версия, URL и SHA опубликованного согласия (ФТЗ §4.5.3, UX B-007) | LEGAL_BLOCKED `LEGAL_CONSENT_B007_RELEASE_GATE.md` | Реальные реквизиты и юридически подписанный текст/публикация | Юрист+оператор ПД; повторная проверка SHA |
| DLC-P0-09 | Расчётные правила, ставки, моратории, исключения и привязка к первичным источникам (ФТЗ §9/§15) | Базовый расчёт есть; расширенный редактор #133 — отдельный Draft | Юридическая ревизия, контрольные сценарии, governed production revision | Legal reviewer подписал версию, ссылки/даты/хэши |
| DLC-P0-10 | Историческая чувствительная информация внутри immutable AuditLog / backup после удаления (ФТЗ §14–17, privacy) | #199/#207 предотвращают новые/публичные утечки, #198 OPEN | Безопасный aggregate-only inventory, законный режим хранения/криптографическая редакция; не менять подписанную цепь произвольно | Legal/privacy+security signoff, backup/restore доказательство |
| DLC-P0-11 | Безопасный приём/шифрование/версионирование документов (ФТЗ §10, UX10) | Encrypted source и review есть; ClamAV #171 + pikepdf/OCR #173 Draft | Закрыть отсутствие scanner/derivative runtime; перформанс/права/таймаут, не терять source | Проверка реальных PDF/изображений в изолированном Docker |
| DLC-P0-12 | Оплаты: signed provider webhook, idempotency, суммы/этапы и 10% от подтверждённых поступлений | SOURCE tests / production provider NOT VERIFIED | YooKassa sandbox M1/M2, replay/mismatch и финансовая сверка | UAT-06/08/09/13, финансы+интегратор |
| DLC-P0-13 | Переписка, подтверждение отправки, retries/outbox, минимальный audit (ФТЗ §14; UX C-MSG/N-*) | DRAFT #199/#204–#206/#211 | Реальная доставка/отказ Telegram, смешение дел, несохраняемый source-message | UAT-11/13, 3 разных аккаунта |
| DLC-P0-14 | Рабочие места Admin/Lawyer, RBAC, MFA и SLA (ФТЗ §11–13; UX A-001…A-009, L-001…L-010) | CODE_PRESENT, browser E2E; #208 MFA hardening Draft | Реальные роли, скриншоты iPhone/iPad/desktop, анонимизированная запись приемки | UAT-12 + заблокированные чужие действия |
| DLC-P0-15 | Разделы Back/Cancel/Timeout/Error/Loading и восстановление state (UX B-001…B-028, UX9–UX17) | Автоматические маршруты есть, ручной визуал неполон | Пройти каждый экран/кнопку на реальном Telegram, два активных дела, отказ/рестарт | UX экранная карта + реальные записи, UAT-01…03/13 |
| DLC-P0-16 | Redis FSM и PostgreSQL вместо memory/SQLite в реальном MVP (ФТЗ §2, §17.10) | В CI есть реальные миграции PostgreSQL, Redis tests | Верификация реального Timeweb staging + restart, отдельные volumes | UAT-13/14/16, infra owner |
| DLC-P0-17 | Резервное копирование / восстановление encrypted документов, ключей, аудита, БД (ФТЗ §17.10) | CI disposable PG backup/restore GREEN #209 | Реальный операторский restore drill и key continuity на staging | UAT-14, точный SHA/image/schema |
| DLC-P0-18 | Честный release status, exact-SHA/Compose boundary и no-launch switch | DRAFT_TESTED #209 (908 PASS/8 SKIP ×2) | Независимое ревью, интеграция; правила выдачи подписанного admission | UAT-16; production HOLD до внешних attestations |
| DLC-P0-19 | Независимое ревью зависимых Draft #188–#211 | OPEN; у #209 нет принятых ревью | Review безопасности/миграций/приватности и порядок объединения | Approved review каждого рискованного сегмента, final SHA |
| DLC-P0-20 | Реальные Telegram/Timeweb/YooKassa и подписи клиента | LIVE-001 #185 OPEN | 3 разных тестовых Telegram аккаунта; отдельный staging; UAT-01…16 | Релизное досье, 0 P0/P1 дефектов, заказчик+оператор |

## 3. P1 — согласованные улучшения сверх исходного MVP (не терять и не выдавать за merged)

| ID | Улучшение | Источник и реальная стадия | Следующее обязательное действие |
|---|---|---|---|
| DLC-P1-01 | **Самоподача М1**, 15 000 ₽, только банковский перевод, 4 документа, 3 календарных дня после подтверждённого поступления | Заказчик, Draft [#137](https://github.com/PetrFedin/digital-legal-concierge/pull/137). Это режим услуги **М1**, не М3 | Rebase/cherry-pick с reviewed современной М1, freeze реквизитов/назначения, банковская сверка, реальная SMTP доставка/Message-ID, signed/unsigned transfer-act cases. Не активировать продажи до UAT |
| DLC-P1-02 | Governed редактор юридических правил и ставок, source provenance, моратории/лимиты, review/hash publication | Draft [#133](https://github.com/PetrFedin/digital-legal-concierge/pull/133), divergent stack | Архитектурное сопоставление с расчётом из #188–#193, юридически утверждённая версия, никаких автопубликаций |
| DLC-P1-03 | Нейтральный свежий /start, шестишаговый preview без создания дела, явное «Продолжить общение» | Draft [#136](https://github.com/PetrFedin/digital-legal-concierge/pull/136) и частично #189–#193 | Diff двух механизмов, сохранить один canonical FSM, проверить Back/Cancel/timeout/double-tap |
| DLC-P1-04 | Malware quarantine / документация угроз | Draft [#171](https://github.com/PetrFedin/digital-legal-concierge/pull/171) | Docker ClamAV/clamd на разрешённом стенде, real scanner fail-closed, расходы/нагрузка |
| DLC-P1-05 | pikepdf/OCRmyPDF derivatives без переписи юридического source | Draft [#173](https://github.com/PetrFedin/digital-legal-concierge/pull/173), depends #171 | Оценка migration 0032 и текущей ветки 0020, обработка PDF/сканов и RAM/CPU |
| DLC-P1-06 | Прозрачный кабинет юриста и администратора, упрощённые тексты, очереди, дата/следующий шаг, audit UX | UX A-/L-/N-каталоги и поздний запрос об упрощении | Human mobile/tablet/desktop UX QA, исправить сложные экраны, проверить клавиатурную доступность |
| DLC-P1-07 | Улучшенный поиск и сообщения, аналитика SLA/нагрузки, воспроизводимость спорного действия | ФТЗ §12–14 + Integration Master Plan | Только после авторитетного ledger, не дублировать Case/Payment/Document |
| DLC-P1-08 | Регулярное versioned подтверждение сроков/платежей/документов клиентом | Запросы сопровождения и UX; текущая audit framework | Показывать доказуемые этапы без ложных заверений о процессе в суде |

## 4. P2 — большой стратегический план (не блокирует выпуск М1/М2)

Ниже **не подтверждённое внедрение**, а элементы `DIGITAL_LEGAL_CONCIERGE_INTEGRATION_MASTER_PLAN_2026-10-01.md`. Каждый требует отдельного согласования затрат, угроз, законности и scope; в MVP не создавать автоматический третий маршрут.

| Волна Master Plan | Предмет | Disposition |
|---|---|---|
| Phase 3–6 | PII redaction, Legal Timeline Authority, Search, OPA/OpenFGA | DESIGN_ONLY, оценить с учётом действующего RBAC |
| Phase 7–11 | Document assembly/signature, secrets, audit anchoring, Schemathesis/Trivy | DESIGN_ONLY / отдельные security RFC |
| Дополнительная волна | pyHanko, Cosign signing, OpenTelemetry | DESIGN_ONLY, полезно для институционального доверия |
| Процессуальная волна | Procedural Deadline Authority, Evidence Bundle, Legal Source Registry, Redline | DESIGN_ONLY, зависит от подтверждения юриста |
| Archival conformance | PDF/A, immutable derivative policy, preservation manifest | DESIGN_ONLY, зависит от документа/шифрования |
| Web/email evidence | Проверяемое доказательство web/email, DKIM verification | DESIGN_ONLY, privacy/chain of custody сначала |
| Evidence graph / LTV | Case Argument Graph, PAdES/LTV trust policies | DESIGN_ONLY, legal expert-in-the-loop |
| Premium коммерция | Secure Settlement Room, versioned offers, negotiation evidence | DESIGN_ONLY, только после accepted М1/М2 |
| Enterprise | External Counsel Workspace, Filing Readiness Gate, Handoff | DESIGN_ONLY, enterprise ACL/taxonomy отдельно |
| Portfolio moat | Multi-case operations cockpit, bulk intake, shared evidence | DESIGN_ONLY, не смешивать tenant/case |
| Embedded platform | Partner API, white-label, scoped webhooks | DESIGN_ONLY, только новая authority/consent |
| Institutional | Case Readiness Standard, Credential, Professional Network | DESIGN_ONLY, юридическая стандартизация и рынок отдельно |

## 5. Жёсткая последовательность дальнейших действий

1. **Privacy proof:** Draft #211, свежий exact-head CI на SQLite/PG, негативные тесты до physical deletion.
2. **Dependency review:** инвентаризация и независимое ревью всех P0 PR, зафиксировать divergence #197→#199 (test change имеется по blob), выбрать безопасную стратегию cherry-pick/integration без дублирования механизмов и непроверенной миграции.
3. **Privacy/legal decisions:** отдельные решения #196/#198 и B-007/rate-policy; не присваивать «полное удаление» при физически сохранённой исторической PII.
4. **Isolated staging:** отдельные PostgreSQL/Redis/storage/Telegram/YooKassa sandbox, ключи, HTTPS, точный SHA, backup restore и мониторинг, без production/новых платных ресурсов.
5. **UAT:** 16 сценариев по исходному журналу, включая старые callbacks, финансовый ledger и 3 разных клиента; прикладывать только редактированные evidence/ссылки.
6. **Controlled merge:** утверждённая цепочка, повторный CI и restored runtime **на новом объединённом SHA**, независимые security/QA approvals.
7. **Approved extensions:** сравнение #137/#133/#136/#171/#173, отдельные безопасные PR. Институциональные модули P2 не открывать как production claims.
8. **Production decision:** только после подписанного комплекта, роли/ответственности, rollback/recovery; иначе **HOLD**.

## 6. Непреложные ограничения качества

- Никогда не переносить строки старого AuditLog при удалении «для красоты» без юридически согласованной криптографической стратегии.
- Никогда не считать staged fake-payment, mock Telegram, health=200, local file presence, 908 PASS или скриншот доказательством реальной юридической/клиентской приёмки.
- Никаких DB/file/provider миграций в работающих клиентских данных ради синтетического acceptance.
- Новые возможности не должны внедрять второй workflow engine, второй Document authority, независимый Payment ledger либо M3.
- Для каждой новой задачи: источник → owner → code commit/PR → exact-head tests → security negative path → live UAT → acceptance receipt.
