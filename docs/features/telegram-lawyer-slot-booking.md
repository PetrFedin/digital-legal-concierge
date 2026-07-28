# Telegram-запись на юридическую консультацию

## 1. Назначение

Сценарий позволяет клиенту пройти полный путь выбора времени юридической
консультации в Telegram:

1. выбрать способ поиска;
2. выбрать юриста, дату или ближайшее время;
3. удержать свободный слот;
4. перейти к оплате;
5. увидеть состояние записи в «Моём деле»;
6. после подтверждения изменить время или отменить консультацию.

Telegram-слой не изменяет `ConsultationSlot`, `Consultation`, `Payment` или
`Case.assigned_lawyer_id` напрямую. Изменения выполняются доменными сервисами,
а commit остаётся во внешнем orchestration-слое.

## 2. Точка входа

Основная точка входа находится в
`app/bot/screens/consultation_entry.py`.

После описания ситуации и необязательного шага документов консультация
переходит в `SLOT_PENDING`. Callback `consult_slot_open` обрабатывается
подключённым раньше legacy-handlers router из
`app/bot/screens/consultation_selection.py`.

## 3. Варианты выбора

Клиенту доступны четыре режима:

- ближайшее свободное время;
- выбор юриста;
- выбор даты;
- «Мой юрист» — только при назначенном юристе и наличии его будущих слотов.

Внутренние показатели нагрузки, лимит, административный e-mail, внутренний
телефон и идентификаторы клиенту не показываются.

## 4. Query-слой выбора

`ConsultationSlotSelectionService` в
`app/domain/consultations/slot_selection_service.py` строит client-safe DTO:

- `ClientLawyerOption`;
- `ClientDateOption`;
- `ClientSlotOption`;
- `ConsultationSlotSelection`.

Сервис проверяет:

- владельца `Case`;
- маршрут M2;
- согласованные статусы `Case` и `Consultation`;
- активность юриста;
- остаточную capacity для нового назначения;
- уже назначенного юриста дела;
- будущее время слота;
- статус `available`;
- отсутствие связи слота с другой консультацией;
- отсутствие пересечения с другой подтверждённой консультацией клиента.

Окно по умолчанию — 14 дней. В запрос не загружаются исторические слоты.

## 5. Контроль нагрузки

Новый юрист допускается к выбору только при наличии остаточной capacity.

Если юрист уже назначен делу, достигнутый лимит не блокирует продолжение
работы по этому делу. Просмотр слотов не назначает юриста и не выполняет
скрытое переназначение.

Каноническая mutation-точка назначения —
`CaseService.assign_lawyer()`. `CaseAssignmentService` предоставляет
workload read-model и делегирует назначение каноническому сервису.

## 6. Ближайшее время

Варианты сортируются по:

1. началу;
2. окончанию;
3. имени юриста;
4. идентификатору слота как стабильному внутреннему tie-breaker.

Карточка показывает дату, интервал, имя, специализацию, длительность и формат.

## 7. Выбор юриста

Показываются только активные юристы, у которых есть доступный слот.

Карточка содержит:

- имя;
- специализацию;
- ближайшее свободное время;
- формат консультации.

Поля `phone`, `email`, `workload_limit`, `current_workload` и
`available_capacity` в клиентский DTO не входят.

## 8. Выбор даты

Показываются только даты, на которые существуют доступные варианты.

На одной странице выводится не более семи дат. Выход за диапазон не создаёт
бесконечную пагинацию: клиенту предлагается возврат на первую страницу.
После каждого callback данные повторно загружаются из БД.

## 9. Callback-контракт

Навигационные callbacks централизованы в
`app/bot/consultation_booking_callbacks.py`.

Используются формы:

- `consult_select:mode:<mode>`;
- `consult_select:lawyer:<reference>`;
- `consult_select:lawyer_dates:<reference>`;
- `consult_select:dates:<page>:<lawyer_reference>`;
- `consult_select:date:<YYYYMMDD>:<lawyer_reference>`;
- `consult_slot_select:<slot_reference>`;
- `consult_reschedule_date:<YYYY-MM-DD>`;
- `consult_reschedule_slot:<slot_reference>`.

Callback не содержит `client_id`, Telegram ID, сумму, платёжную ссылку,
персональный текст или секрет. Numeric reference считается недоверенной
ссылкой и повторно проверяется сервером. Тесты покрывают лимит Telegram
64 байта, включая 64-bit reference.

## 10. Форматирование Telegram

Бот создаётся без глобального HTML/Markdown `parse_mode`. Все сообщения
отправляются как plain text. Поэтому значения с `<`, `>`, `&` и Markdown
символами не интерпретируются как разметка и не требуют HTML escaping.

Если в будущем будет включён parse mode, перед включением потребуется единый
escaping helper и отдельная регрессия динамических полей.

## 11. Атомарный hold

Первичный выбор вызывает существующий
`ConsultationService.reserve_pre_payment_slot()` и `SlotService.hold_slot()`.

Сервис повторно проверяет:

- владельца дела;
- единственную активную консультацию;
- статус этапа;
- существование слота;
- будущее время;
- статус `available`;
- отсутствие действующего другого hold.

Срок удержания задаётся доменным `SlotService`, а не Telegram handler.
Повторный выбор того же действующего hold идемпотентен.

## 12. Истечение hold

Единая операция `SlotService.release_expired_holds()`:

- возвращает слот в `available`;
- очищает hold и связь с консультацией;
- очищает `Consultation.slot_id`, юриста и `scheduled_at`;
- возвращает консультацию и дело к выбору времени;
- пишет `CONSULTATION_SLOT_HOLD_EXPIRED`.

Операцию используют scheduler и открытие «Моего дела». Повторный cleanup
идемпотентен. Booked-слоты не затрагиваются.

## 13. Payment lifecycle

Telegram использует существующие `PaymentService`,
`ConsultationPaymentLifecycleService` и `PaymentWebhookService`.

Повторное открытие оплаты переиспользует активный Payment. Клиентский экран:

- не показывает numeric `Payment.id`;
- не показывает DEV-кнопки;
- не предлагает старую ссылку для `PAID`, `FAILED`, `CANCELLED`, `REFUNDED`
  или `EXPIRED`;
- скрывает ссылку при `manual_review_required`;
- не утверждает наличие чека, которого нет в модели.

Подтверждение оплаты выполняет webhook или существующий local-only серверный
handler. Клиент не может передать сумму или статус через callback.

## 14. Подтверждение записи

После успешной оплаты lifecycle подтверждает слот и переводит консультацию в
этап ожидания подтверждения юриста. Назначение юриста делу проходит через
канонический assignment service.

Повторный webhook должен оставаться идемпотентным и не создавать второй слот,
Payment или назначение.

## 15. «Моё дело»

`app/bot/screens/my_case.py` показывает фактическое состояние связки
`Case + Consultation + ConsultationSlot + Payment`:

- статус записи;
- дату и начало;
- окончание;
- длительность;
- московский часовой пояс;
- имя юриста;
- формат;
- состояние оплаты;
- остаток hold;
- следующий шаг.

Перед отображением выполняется idempotent cleanup истёкших holds. Следующее
действие вычисляется по реальным Consultation, Slot и Payment, а не только по
`Case.next_action`.

В клиентский текст не попадают внутренний телефон, e-mail, storage path,
workload, capacity, raw enum или numeric database ID.

## 16. Атомарный перенос

`ConsultationRescheduleService` заменяет подтверждённый booked-слот внутри
savepoint.

Последовательность:

1. блокируется Consultation;
2. блокируется текущий booked-слот;
3. блокируется новый слот;
4. проверяется активность и совпадение юриста;
5. проверяются пересечения;
6. старый слот временно освобождается внутри savepoint;
7. новый слот переводится в booked;
8. Consultation связывается с новым временем;
9. создаются audit и клиентское уведомление;
10. внешний слой выполняет commit.

При любой ошибке savepoint восстанавливает старый booked-слот и связь
Consultation. Payment не изменяется. Повторный выбор уже установленного слота
идемпотентен.

Первичные callbacks `consult_slot_select:*` после перехода в BOOKED не дают
право на перенос. Перенос доступен только через отдельный
`consult_reschedule*` flow.

## 17. Отмена

Отмена требует отдельного подтверждения.

`ConsultationService.cancel()`:

- повторно проверяет владельца и связь с делом;
- блокирует Consultation;
- блокирует только связанный с ней слот;
- запрещает отмену `DONE`, `CLOSED` и `DECLINED`;
- освобождает слот;
- очищает `Consultation.slot_id`;
- переводит консультацию в `CANCELLED`;
- синхронизирует M2 Case;
- пишет один `CONSULTATION_CANCELLED` audit event.

Payment не удаляется и не переводится в `REFUNDED`. Интерфейс не обещает
автоматический возврат средств, потому что refund lifecycle в проекте пока
отсутствует.

## 18. Уведомления

`NotificationEngine` дедуплицирует одинаковое отрендеренное событие по
комбинации:

- event code;
- case;
- user;
- recipient label;
- text.

Перенос создаёт клиентское `CONSULTATION_RESCHEDULED`. Повторный вызов с той
же датой переиспользует существующую запись; новая дата создаёт новое
уведомление. Payment reminder также не дублируется при повторном scheduler run.

Текущая модель `Notification` не содержит отдельного `lawyer_id` или
`recipient_type`. Поэтому новые события управления консультацией не имитируют
адресацию юристу через клиентский `user_id`.

Дедупликация не имеет уникального constraint БД и не является полной защитой
от двух строго одновременных транзакций. Для абсолютной конкурентной гарантии
потребуется отдельный fingerprint и миграция.

## 19. Audit

Ключевые события:

- `LAWYER_ASSIGNED`;
- `CONSULTATION_SLOT_RESERVED`;
- `CONSULTATION_PAYMENT_PENDING`;
- `CONSULTATION_RESCHEDULED`;
- `CONSULTATION_CANCELLED`;
- `CONSULTATION_SLOT_HOLD_EXPIRED`.

Платёжные ссылки, секреты провайдера и полный callback в audit не сохраняются.

## 20. Производительность

Client slot selection выполняет фиксированные четыре SQL-запроса:

1. owned Case + Consultation;
2. capacity активных юристов;
3. свободные Slot + Lawyer;
4. подтверждённые интервалы текущего клиента.

Query-count test подтверждает одинаковое число запросов для двух и двадцати
юристов. Количество запросов не растёт линейно с количеством результатов.

## 21. Empty states

Предусмотрены состояния:

- нет доступных юристов;
- нет дат;
- нет времени на выбранную дату;
- назначенный юрист без свободных слотов;
- stale callback;
- истёкший hold;
- ручная проверка платежа;
- отменённая или завершённая консультация.

Каждый экран предлагает актуальное продолжение, «Моё дело» или главное меню.

## 22. Миграции

Для Telegram booking flow новые таблицы и колонки не добавлялись. Используются
существующие `Case`, `Consultation`, `ConsultationSlot`, `Payment`, `Lawyer`,
`Notification` и `AuditLog`.

## 23. Основные тесты

- `tests/test_case_assignment_capacity.py`;
- `tests/test_consultation_slot_selection_service.py`;
- `tests/test_telegram_consultation_selection.py`;
- `tests/test_consultation_reschedule.py`;
- `tests/test_telegram_consultation_management.py`;
- `tests/test_consultation_cancellation.py`;
- `tests/test_scheduler_consultation_hold_cleanup.py`;
- `tests/test_notification_idempotency.py`;
- `tests/test_telegram_payment_presentation.py`;
- `tests/test_telegram_my_case_consultation.py`;
- `tests/test_telegram_callback_contracts.py`.

## 24. Команды проверки

```bash
python -m compileall -q app tests
pytest -q
ruff check app tests
git diff --check
```

## 25. Текущие ограничения

- Репозиторий не содержит отдельного refund lifecycle.
- В Lawyer нет public-contact полей или признака публикации контактов.
- В Notification нет отдельной связи с получателем-юристом.
- Exact-content notification dedupe не заменяет уникальный fingerprint БД.
- SQLite-тесты не доказывают PostgreSQL-семантику `SELECT FOR UPDATE` и
  exclusion constraints при реальной конкуренции.
- Активная кнопка provider polling не добавлена: источником подтверждения
  оплаты остаётся webhook.
- Подписка клиента на появление новых слотов не реализована, потому что для неё
  нет существующей persistent-модели.
