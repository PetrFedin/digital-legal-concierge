# Digital Legal Concierge — проверенный LegalTech-бенчмарк и программа расширения
**Дата проверки публичных продуктовых страниц:** 20.10.2026. **Кодовая точка сравнения:** \`1b94dbe751fd7814b5efda4965fad689fdb20a4b\` (Draft #221, 4/4 CI SUCCESS, 957 PASS/10 SKIP). **Окружение:** source/CI, не production. **Статус:** исследование и proposed roadmap, НЕ внедрение новых клиентских функций.

## 0. Методология, границы и защита от недостоверных статусов
- **Канон MVP:** пользовательский маршрут только М1 (взыскание просрочки передачи по ДДУ 214-ФЗ) или М2 (консультация), Telegram-first, клиентский отдельный web-портал исключён. Одна правовая State Machine; решение о ведении дела принимает юрист. Источники: две утверждённые спецификации из \`source_documents/\` и \`docs/CANONICAL_MVP_SPEC_2026-10-03.md\`.
- **Не смешивать:** (1) официально заявленную производителем возможность; (2) исходники DLC; (3) автоматический прогон DLC на точном SHA; (4) живой UAT; (5) коммерчески доступный/одобренный legal workflow. Сайты конкурентов изучены как **публичные первоисточники**, их реальные закрытые аккаунты, тарифы, API, uptime и полнота продукта не тестировались.
- **Статусы внутри DLC:** C — код в candidate, source/CI проверка не означает внешнего запуска; P — частично (и недоказанный внешний этап); B — blocked legal/security/external UAT; D — отдельно сформулированный дизайн; N — отсутствие доказанной реализации в текущем кодовом кандидате, НЕ утверждение «нет ни одной строки кода».
- **Экономика:** цену лицензий и сроки реализации НЕ угадываем. Нет доказанных затрат и сроков — пишем TBD, до выбора провайдера требуем RFQ, TCO, локализацию данных, лицензионную и юридическую экспертизу.
- **Правовые ограничения:** не копировать чужой код, шаблоны и контент; не парсить закрытые кабинеты; не заявлять вероятности выиграть суд без утверждённой методологии; не генерировать автоматическое окончательное юридическое заключение.
- **Релиз:** сохранение базовых М1/М2 и будущих пользовательских расширений (например, отдельный режим самоподачи в пределах М1) не даёт разрешения включать их в live. New Wave после подписанной приёмки, по отдельным feature flags / PR.

## 1. Подтверждённые российские аналоги (официальные источники)
| Продукт | Функции, заявленные самим поставщиком | Что перенять как паттерн (не копия) | Доказательство |
|---|---|---|---|
| Правовед.ru | Публичный вопрос, частный платный чат, выбор конкретного юриста, платные документы, отзывы/профили, консультации по телефону | Краткий вход, human routing, письменный результат, feedback после консультации, честная очередь/ожидание | https://pravoved.ru/faq/ ; https://pravoved.ru/ |
| СберПраво | Каталог консультаций, подготовка документов, судебная помощь, услуга по задержке сдачи объекта, годовые пакеты юридической помощи | Каталог услуг и прозрачная цена до подтверждения, аннулирование/возврат, допускаемый отдельной лицензией план обслуживания | https://sberpravo.ru/ |
| Доступное Право | Связь в чате, видео и аудио, обмен файлами, договор, оплата, готовые решения | Мультиканальность без потери контекста; гарантированная последовательность «задача → юрист → документы → решение» | https://dostupnoepravo.ru/ |
| ПравоДепартамент (ex. Case.one) | Одна карточка судебного дела, рабочий календарь, автоматизированные сценарии, документогенерация, нагрузка юристов, претензия→суд→взыскание | Календарь юридических событий, юридические чек-листы, контроль ручных шагов и назначений, операционная аналитика | https://pravo.tech/product/departament |
| ПравоДела (ex. Casebook) | Судебные карточки/история, уведомления, мониторинг контрагентов, статистика судей, аналитика, шаблоны обращений и онлайн-подача в поддерживаемых системах | Отдельный лицензируемый read-only adapter событий и процессуальных актов, сверяемый юристом; не выдавать вероятность как гарантию | https://pravo.tech/product/dela |
| Doczilla / Doczilla Pro | Вариативные шаблоны договоров и документов, совместное редактирование, библиотека формулировок, единое хранилище | Варианты одобренных договоров/претензий, управляемый каталог формулировок, контроль версии при подписи | https://doczilla.ru/ |
| FreshDoc | Анкетный конструктор документов, перенос Word/1C, хранение в папках и реестрах, поиск по атрибутам | Повторное применение проверенных шаблонов, единые поля дела, быстрый поиск версий без смешения юр. сущностей | https://www.freshdoc.ru/ |

## 2. Подтверждённые международные аналоги (официальные источники)
| Продукт | Функции, заявленные самим поставщиком | Что перенять как паттерн | Доказательство |
|---|---|---|---|
| Clio Manage/Grow/Clients | Intake/лиды → matters, клиентский secure portal, сообщения и документы, календарь, тайминг, счета/платежи, автоматические задачи, отчёты | Связать lead→matter→task→document→invoice, защищённую выдачу результатов клиенту и прозрачный следующий шаг | https://www.clio.com/manage/ ; https://www.clio.com/clients/ |
| MyCase | Кейс-менеджмент, календарь и автоматизация задач, шаблоны/документы/eSignature, клиентский портал, расходы/время и billing, аналитика | Общий кабинет событий и контролируемые финансовые метрики; административная витрина результатов по исходным событиям | https://www.mycase.com/products/case-management/ ; https://www.mycase.com/features/ |
| Lawmatics | Legal CRM, intake формы, квалификация и follow-up, назначение встреч, персонализация, workflow с triggers/actions, eSign+form+booking+payment packet | Версионная intake-схема, автомаршрутизация с ручным override, consent/contract/payment одной проверяемой цепью | https://www.lawmatics.com/legal-crm ; https://help.lawmatics.com/en/articles/13117687-e-signature-packets |
| Filevine | Matter/project hub, встроенный документооборот, коммуникации/задачи, портал клиента, отчёты, AI-assisted connected actions | Контекстная лента дела с происхождением данных; governance-first AI: предложить действие, юрист подтверждает | https://www.filevine.com/platform/case-management-software/ ; https://support.filevine.com/hc/en-us/articles/28865773457563-Client-Portal-by-Filevine |
| Smokeball | Централизованное matter, Word/Outlook интеграции, автоматическое время, шаблоны документов, клиентский portal, финансы, задачи, отчёты, AI | Меньше повторного ввода, автоматическое делопроизводство, измерение загрузки/качества обслуживания | https://www.smokeball.com/features |
| Rocket Lawyer | Guided legal documents, online customization/signing, review/understand documents, AI document assistant and lawyer access | Intake → проверенная структура фактов → управляемый черновик документа → одобрение юриста | https://www.rocketlawyer.com/legal-documents |
| LegalZoom | Forms, eSignature, поддержка юристов, ограниченные услуги по подписке | Чётко разделить бесплатную предварительную информацию, консультацию, подготовку документа и платное ведение | https://www.legalzoom.com/attorneys/legal-plans/personal/ ; https://help.legalzoom.com/docs/pricing-plans |

*Ограничение интерпретации:* перечисленные функции принадлежат указанным продуктам/тарифам или заявлены поставщиком; это не фактическая проверка доступности каждой функции всем пользователям и не факт наличия действующих API в РФ.

## 3. Сопоставление с DLC (не путать наличие исходников с подтверждением production)
| № | Capability / бизнес-результат | Наше состояние на кандидате #221 | Недостающий контракт / усиление | Источник конкурентного паттерна | Приоритет |
|---|---|---|---|---|---|
| 01 | Telegram intake, продолжение черновика и возвраты | C, внешний Telegram UAT B | Мобильный тест every B-001…B-028, все Back/Cancel/Error/Timeout | Правовед, Lawmatics | P0 уже в релизном gate |
| 02 | Структурированное дело: клиент, собственник, юрист, статусы | C | Timeline единый и role-safe, кликабельные source/event IDs | Clio, ПравоДепартамент | P1 |
| 03 | М1/М2 State Machine и перевод с решением юриста | C; внешний UAT B | Decision receipt / ответственность / no unsupported third route | DLC спецификация | P0 |
| 04 | Расчёт ДДУ с историческими спецпериодами | B (#210) | Версионный legal rule engine, interval breakdown, квалификационная матрица и подпись юриста | Доменные требования DLC, не чужой калькулятор | **P0** |
| 05 | Документы: encrypted source, версии, review | C/P | Двустороннее однократное получение архивного файла клиентом; channel consent | Clio portal / Doczilla | **P0 #196** |
| 06 | History/retention/audit | P/B (#198) | Доказанное управление historic sealed PII + резервными копиями | Управление данными | **P0** |
| 07 | Запрос и сбор недостающих документов | C/P | Чеклист на стадии дела, health/status качества PDF, юрист отмечает evidence completeness | Filevine, Doczilla | P1 |
| 08 | Контракт → подпись → платеж → юридический handoff | P/B live provider | Подтверждение юридической силы способа подписи, versioned hash контракта, YooKassa real sandbox receipts | Lawmatics packets, Rocket Lawyer | P0 approval / P1 eSign |
| 09 | Управление слотами и напоминаниями | C/P | Конфликт слота, двойная бронь, timezone-экран, календарь юриста | MyCase, Lawmatics | P1 |
| 10 | Центр сообщений клиента и юриста | C, Telegram live B | Доставка/повтор/UNKNOWN, read receipts без ложного «прочитано», urgent override | Правовед, Filevine | P0/P1 |
| 11 | Персональные административные роли и MFA | C (новый #221), external UAT B | Audit denial/export event, continuous route inventory, 2-person approval sensitive actions | Clio, Smokeball | P0 security review |
| 12 | Экспорт и аналитика управления | C/P | Audit каждой выгрузки, privacy aggregate KPI, data dictionary, drill-down только по ACL | ПравоДепартамент, Clio | P1 |
| 13 | SLA, очереди, назначения | C/P | Skill-based routing, workload balancing; human override with reason | Правовед, ПравоДепартамент | P1 |
| 14 | Судебные слушания, акты и сроки | C/P | Versioned manual source + optional licensed court-monitor adapter, confirmation by lawyer | ПравоДела | P1/P2 |
| 15 | Исполнительное производство, фактические поступления и 10% | C/P | Partial receipts, event/source reconciliation, no invoice before actual credited receipt | Доменные правила DLC | P0 UAT |
| 16 | Повторное ведение многих дел одним клиентом | C/P | Active vs closed case switch, isolation after replay; virtual case folder | Clio, MyCase | P0 UAT |
| 17 | Шаблоны претензий/договоров с версии/approval | P/D (см. master plan) | Dynamic fields, lawyer preview, hash, human approval, PDF derivative + source link | Doczilla/FreshDoc/Smokeball | P1 |
| 18 | Conflict of interest / юридическая квалификация | N/D | Проверка юриста/сторон по согласованным правовым основаниям, без автоматических отказов | Lawmatics intake pattern | P2 |
| 19 | Безопасный client portal вне Telegram | N (прямо исключён из MVP) | Только пост-MVP, если Telegram не отвечает требованиям по доставке файлов | Clio, Filevine | P2 conditional |
| 20 | Анализ договоров/судебных актов AI | D | Retrieval с цитируемыми выдержками и версией правового источника; любые юридические утверждения на review | Rocket Lawyer, Filevine | P2 |
| 21 | Автоматизация дел/задач trigger→action | C/P (notification/workflow) | Visual workflow editor с безопасными зарегистрированными action IDs, dry-run, no arbitrary transitions | Lawmatics, MyCase | P2 |
| 22 | Канальный конструктор подписок/тарифов | N/D | Пост-MVP, без изменения текущего контракта 30k/70k/10%, approval товаров/условий | СберПраво, LegalZoom | P2 |
| 23 | Часы юристов/маржинальность/стоимость обращения | N/D | Event→time attribution, internal activity, finance reconciliation, not billing clients by stealth | Smokeball, Clio | P2 |
| 24 | Партнёрский/white-label API | D в master plan | Tenant isolation, narrow scopes, partner consent, signed webhooks | Master plan | P3 |
| 25 | Консультация по видео/аудио | N/D | Отдельное согласие и приватность звонков; не расширять MVP без владельца | Доступное Право | P2 |
| 26 | Электронная подача в суд / court feeds | N/D | Только при доказанном публичном API/договоре и допустимости обработки данных | ПравоДела | P3 |
| 27 | Интеллектуальный «паспорт готовности дела» | D в master plan | Fail-safe operational checklist по facts/evidence/tasks; не оценка победы в суде | Собственная продуктовая гипотеза | P2 |
| 28 | Исполнимое юридическое досье / chain of custody | Частично в audit/backup | Source hashes, source classifications, evidence manifest, export review | Filevine + собственное развитие | P1 |

## 4. Главное конкурентное преимущество DLC (проектное, не маркетинговое доказательство превосходства)
**Специализированная непрерывность от расчёта до реального поступления средств:** исходные факты, договор, юридические документы, претензия, 30 дней, суд, 70k, исполнение, частичные поступления и 10% от подтверждённой суммы связываются с одной Case authority и immutable evidence. Это проектная цель архитектуры, а не утверждение, что все конкуренты не умеют такое или что DLC уже прошёл живую приёмку.

### UX-архитектура: три вида, один authority
- **Клиент Telegram:** где дело, что дальше, какие документы, какая юридическая оговорка к сумме, какие деньги/этапы подтверждены. Только одна primary CTA по статусу; отсутствие «пройдите на новый портал» в MVP.
- **Юрист:** my cases, unresolved evidence, SLA, source-backed calculation sections, timeline court events, кнопка «подтвердить / запросить / отклонить с причиной».
- **Админ:** очередь, SLA, назначения, сумма платёжных событий/фактических поступлений, ошибки доставки, «требует ручной проверки». Без ручного переписывания Case.status / финансового ledger.

## 5. Порядок выпуска и зависимостей
### Gate 0 — обязателен до любого публичного использования
1. P0 #196: security-reviewed transport исходных архивных PDF клиенту; отдельный channel consent, одноразовый грант, provider ack.
2. P0 #198: documented immutable historic audit personal-data lifecycle; backup erasure policy and signed privacy decision.
3. P0 #210: юридически утверждённые сегменты 214-ФЗ/ПП326; fall-closed for unsupported periods.
4. P0 B-007: реальные данные оператора ПД, юридическая публикация, version+hash+consent event.
5. P0 #219: **полный** staff / case/exports endpoint ACL inventory, а не только один роутер. #221 draft 957 pass / 10 skip, 4/4 CI, независимое ревью ещё не выполнено.
6. P0 #185: отдельный Timeweb stage, 3 отдельных Telegram тестовых клиента, YooKassa sandbox/webhook, договор, backup/restore и UAT-01…16, подписанные заказчиком.

### Wave 1 — post-MVP без новых клиентских маршрутов
- Common Event Timeline / Evidence Desk: просмотр связей case→message→document→task→payment→court event; нельзя раскрыть удалённые/retention-restricted сущности через audit.
- Доказательственный чек-лист по стадии + понятный следующий шаг; business rule versioning.
- Template registry: reusable reviewed clauses/approved signature path; signed/hash manifest.
- Dashboard KPI: time-to-first-response, intake→review, review→accept, document rework, notification failures, SLA breach, cash-to-ledger reconciliation.
- Secure export audit: event без PII + access-policy-version + staff_id; no CSV token query.

### Wave 2 — only feature-flagged, funded and separately approved
- Document scan and searchable derivative (ClamAV, OCRmyPDF/Docling), source stays immutable encrypted.
- AI copilot **draft only**: source excerpts and links, cite effective date, human approval before any notification or legal action; abstain on missing source.
- Processual calendar and external licensed feeds, typed data provenance, reconciliation workflow.
- Universal intelligent intake templates **within M1/M2**, not new lawsuit classes.
- Role-adapted secure portal if Telegram file-delivery consent demands it.
- Time/cost analytics, appointments, assisted eSign and client review packets.

### Wave 3 — new product/version, requires change request
- Other substantive litigation areas, portfolio claims, external counsel, institutional APIs, settlement room, partner network and commercial subscription. These are already ideas in \`DIGITAL_LEGAL_CONCIERGE_INTEGRATION_MASTER_PLAN_2026-10-01.md\`; none implies live implementation.

## 6. Технические контракты будущих модулей (перед написанием миграций)
- \`LegalSourceRevision\`: publisher, document URL, scope, published_at, effective interval, immutable content hash, checked_at, approved_by, source_class, supersedes.
- \`LegalRuleApplicability\`: rule_revision, claim kind, fact predicates, applicable intervals, exclusions/caps, approved tests; **no client amount** if interpretation unsupported.
- \`CalculationInterval\`: factual start/end, inclusive/exclusive convention, rate basis, exclusion reason, segment subtotal, rule/source version. Final preview receipt includes itemization fingerprint; older receipts immutable.
- \`CaseEvidenceLink\`: case_id, case party, document/version/source_sha, relation to claim/event, lawyer verification, visibility scope, retention/hold. No plaintext in audit trail.
- \`ClientActionReceipt\`: callback nonce/dedup source, authenticated user_id, case_id, expected status/version, policy revision, consumed_at, result, sanitized notification ID.
- \`OperationalEvent\`: type/idempotency_key, case_id, actor, source_refs, source version, timestamp, restricted payload; materialized «Моё дело» projection references only permissible current data.
- \`DocumentTemplateRevision\`: template_id/version, jurisdiction, reviewed clauses, approved_by/time, variable schema, rendered_sha, signature workflow. Final signature only from reviewed immutable version.
- \`LegalAssistSuggestion\`: input references (no raw PII in search logs), exact source IDs, suggested output, abstain/reasons, confidence classification (qualitative, not pseudo-percentage), reviewer decision.
- \`ExportReceipt\`: actor_id, purpose, export_type, timestamp, row_count, job_ref, policy_version, digest; never bytes, token or client fields in audit.
- \`IntegrationAdapterReceipt\`: provider, license check, query scope, request_id, provider response hash, fetched_at, expiry, reconciliation status, human approval before updating Case.

## 7. Критерии приёмки новых функций (записать до разработки)
| ID | End-to-end check | Отказный сценарий |
|---|---|---|
| BENCH-SEC-01 | staff roles evaluated at request time, all sensitive routers protected incl. exports | revocation/demotion/inactive/rotated MFA; no case or CSV body leaks |
| BENCH-DOC-01 | approved customer receives exactly allowed encrypted archived original once | stale/deleted/foreign/retention/unknown provider receipt denied; no repeat |
| BENCH-LAW-01 | every penalty has segmented basis, effective source and human-approved matrix | unsupported dates/moratorium: no fictitious payable amount or guaranteed recovery |
| BENCH-CASE-01 | client sees own case, stage, timestamp and one next action | multiple clients / stale callbacks / archived case / wrong lawyer denied |
| BENCH-COM-01 | cross-channel communication resolved to one case, dedup by source ID | uncertain delivery not claimed successful |
| BENCH-TPL-01 | document draft linked to approved rule and template revision | unapproved template or corrupted source cannot be signed |
| BENCH-OPS-01 | admin metrics reconcile with immutable payment events and SLA | no phantom paid status / no success fee before actual receipt |
| BENCH-AI-01 | every suggested legal proposition cites exact source/date and reviewer | missing current law, citation drift, PII exfiltration, tool write denied |
| BENCH-REL-01 | external Timeweb, Telegram and YooKassa isolate exact accepted SHA | passed CI without legal/UAT does not flip production-ready |

## 8. Требования к закупке/интеграциям (TCO и governance)
Перед подключением внешних court feeds, OCR/LLM, eSign, email/phone/video или CRM необходимо подтвердить: API/ToS, хранение и трансграничную передачу ПД, операторов/поручения, уровень шифрования, SLA/RPO/RTO, sandbox, стоимость API/запроса/месяца, лимиты и отказные сценарии, право удаления, аудит действия, исходники лицензий, импорт/экспорт и plan of exit. Никакие ключи внешних сервисов, платные инстансы и production-токены не создаются из этой матрицы.

## 9. Отдельный реестр решений: не перепутать «изучено» и «выпущено»
- **Исследовано и обосновано:** 14 продуктов в §1–2 и 28 семейств функций в §3, по публичным официальным страницам. Не гарантируется полная полнота всемирного рынка.
- **Источник DLC:** указан exact SHA (Draft #221) + canonical specs + текущая архитектура. Статусы P/B/D не приравниваются к готовому функционалу.
- **Новые production функции:** 0 включено этой матрицей.
- **Следующий проверяемый результат:** owner-approved change request для Wave 1, отдельный PR и точный SHA + два уровня proof (CI и реальный UAT).
