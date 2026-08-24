import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.case_callback_scope import bound_case_callback
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.screens.consultation_intake import _safe_edit
from app.domain.cases.client_case_scope import latest_completed_strict_m2_case_for_user
from app.domain.consultations.consultation_change_service import (
    ConsultationChangeService,
)
from app.domain.consultations.consultation_intake import consultation_description_ready
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.domain.payments.mode import payments_disabled
from app.domain.payments.refund_service import ConsultationRefundStateConflict
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.presentation_time import to_business_timezone

router = Router()
logger = logging.getLogger(__name__)


def format_date(value):
    return to_business_timezone(value).strftime("%d.%m.%Y")


def format_time(value):
    return to_business_timezone(value).strftime("%H:%M")


def business_date_key(value) -> str:
    return to_business_timezone(value).date().isoformat()


def date_buttons(slots, callback_prefix: str):
    buttons = []
    seen = set()
    for slot in slots:
        # The callback key and the visible date must come from the same outward
        # timezone. Otherwise a UTC slot around midnight can be shown under one
        # day but selected with another day's callback key.
        key = business_date_key(slot.starts_at)
        if key in seen:
            continue
        seen.add(key)
        buttons.append(
            (f"📅 {format_date(slot.starts_at)}", f"{callback_prefix}:{key}")
        )
    return buttons


def booking_recovery_buttons(case_id: int | None = None) -> tuple[tuple[str, str], ...]:
    booking_action = (
        bound_case_callback("consult_booking_start", case_id)
        if case_id
        else "consult_booking_start"
    )
    return (
        ("📅 Выбрать дату и время", booking_action),
        ("✉️ Написать команде", "message_create"),
        ("📁 Моё дело", "my_case_open"),
        ("🏠 Главная", "nav_home"),
    )


def completed_archive_buttons(case) -> tuple[tuple[str, str], ...]:
    """Read-only recovery for stale M2 controls pressed after the consultation was closed."""

    return (
        ("👨‍⚖ Открыть итог консультации", "consultation_result_open"),
        ("💳 Оплаты", "payments_open"),
        ("🗂 Переписка", "message_history"),
        ("📁 Моё дело", "my_case_open"),
        ("🏠 Главная", "nav_home"),
    )


async def _current_booked_context(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        archived = await latest_completed_strict_m2_case_for_user(
            db,
            user_id=user.id,
        )
        return user, archived, None
    consultation = await ConsultationService(db).get_current_for_case(case.id)
    if not consultation or consultation.status != ConsultationStatus.BOOKED:
        return user, case, None
    return user, case, consultation


async def _show_missing_booked_context(callback: CallbackQuery, db, *, action: str) -> None:
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    active = await ctx.case_service.get_active_case_for_user(user.id)
    if active is not None:
        active_number = str(active.case_number)
        await _safe_edit(
            callback,
            "Текущая подтверждённая консультация уже изменилась.\n\n"
            f"Обращение № {active_number}\n\n"
            "Старая кнопка не меняет активное дело. Откройте актуальный шаг консультации.",
            reply_markup=one(
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    archived = await latest_completed_strict_m2_case_for_user(
        db,
        user_id=user.id,
    )
    if archived is not None:
        await _safe_edit(
            callback,
            "🔒 Консультация уже завершена.\n\n"
            "Старая кнопка переноса/отмены больше не изменяет запись, слот или возврат. "
            "Откройте архивный итог и финансовую историю.",
            reply_markup=one(*completed_archive_buttons(archived)),
        )
        return

    await _safe_edit(
        callback,
        "Подтверждённая консультация для этого действия больше не найдена. "
        "Ничего не изменено.",
        reply_markup=one(
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Написать команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_reschedule")
async def consult_reschedule(callback: CallbackQuery, db):
    _user, case, consultation = await _current_booked_context(callback, db)
    if not case or not consultation:
        await _show_missing_booked_context(callback, db, action="reschedule")
        return

    case_id = int(case.id)
    case_number = str(case.case_number)
    consultation_id = int(consultation.id)
    retry_action = bound_case_callback("consult_reschedule", case_id)
    try:
        slots = await SlotService(db).get_available_slots(limit=100)
    except Exception:
        logger.exception("Не удалось загрузить слоты для переноса консультации")
        await _safe_edit(
            callback,
            "Не удалось загрузить свободное время. Текущая запись не изменена.\n\n"
            f"Обращение № {case_number}",
            reply_markup=one(
                ("🔄 Повторить", retry_action),
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if not slots:
        await db.rollback()
        await _safe_edit(
            callback,
            "Сейчас нет свободного времени для переноса. Текущая запись сохранена.\n\n"
            f"Обращение № {case_number}\n\n"
            "Проверьте расписание позже или напишите команде.",
            reply_markup=one(
                ("🔄 Проверить ещё раз", retry_action),
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    date_markup = one(
        *date_buttons(slots, "consult_reschedule_date"),
        ("← Оставить текущее время", "consultation_booked_open"),
        ("🏠 Главная", "nav_home"),
    )
    await db.rollback()
    await _safe_edit(
        callback,
        "🔄 Перенос консультации\n"
        f"Обращение № {case_number}\n\n"
        "Выберите новую дату. Текущая запись останется за вами до отдельного "
        "подтверждения нового времени. Повторная оплата не потребуется.",
        reply_markup=date_markup,
    )


@router.callback_query(lambda c: c.data.startswith("consult_reschedule_date:"))
async def choose_reschedule_date(callback: CallbackQuery, db):
    date_key = callback.data.split(":", 1)[1]
    _user, case, consultation = await _current_booked_context(callback, db)
    if not case or not consultation:
        await _show_missing_booked_context(callback, db, action="reschedule")
        return
    case_id = int(case.id)
    case_number = str(case.case_number)
    retry_action = bound_case_callback("consult_reschedule", case_id)
    try:
        slots = await SlotService(db).get_available_slots(limit=100)
    except Exception:
        logger.exception("Не удалось обновить слоты для переноса консультации")
        await _safe_edit(
            callback,
            "Не удалось обновить расписание. Текущая запись не изменена.\n\n"
            f"Обращение № {case_number}",
            reply_markup=one(
                ("🔄 Повторить", retry_action),
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    selected = [slot for slot in slots if business_date_key(slot.starts_at) == date_key]
    if not selected:
        await db.rollback()
        try:
            await callback.answer(
                "На эту дату свободное время уже закончилось.",
                show_alert=True,
            )
        except Exception:
            logger.debug("Не удалось подтвердить устаревшую дату переноса", exc_info=True)
        await consult_reschedule(callback, db)
        return

    buttons = [
        (
            f"{format_time(slot.starts_at)}–{format_time(slot.ends_at)}",
            f"consult_reschedule_slot:{slot.id}",
        )
        for slot in selected
    ]
    visible_date = format_date(selected[0].starts_at)
    markup = one(
        *buttons,
        ("← Другие даты", retry_action),
        ("Оставить текущее время", "consultation_booked_open"),
        ("🏠 Главная", "nav_home"),
    )
    await db.rollback()
    await _safe_edit(
        callback,
        f"🕐 Новое время на {visible_date}\n"
        f"Обращение № {case_number}\n\n"
        "Текущая запись ещё не изменена. Выберите слот — следующим экраном я попрошу подтвердить перенос.",
        reply_markup=markup,
    )


@router.callback_query(lambda c: c.data.startswith("consult_reschedule_slot:"))
async def choose_reschedule_slot(callback: CallbackQuery, db):
    try:
        new_slot_id = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError):
        await _safe_edit(
            callback,
            "Эта кнопка переноса больше не актуальна. Текущая запись не изменена.",
            reply_markup=one(
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    _user, case, consultation = await _current_booked_context(callback, db)
    if not case or not consultation:
        await _show_missing_booked_context(callback, db, action="reschedule")
        return
    case_id = int(case.id)
    case_number = str(case.case_number)
    retry_action = bound_case_callback("consult_reschedule", case_id)

    try:
        new_slot = await SlotService(db).get_slot(new_slot_id)
    except Exception:
        logger.exception("Не удалось проверить выбранный слот перед переносом")
        await db.rollback()
        await _safe_edit(
            callback,
            "Не удалось проверить выбранное время. Текущая запись не изменена.\n\n"
            f"Обращение № {case_number}",
            reply_markup=one(
                ("🔄 Выбрать время заново", retry_action),
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if not new_slot or str(new_slot.status) != "available":
        await db.rollback()
        try:
            await callback.answer(
                "Это время уже занято или недоступно. Выберите другой слот.",
                show_alert=True,
            )
        except Exception:
            logger.debug("Не удалось показать устаревший слот переноса", exc_info=True)
        await consult_reschedule(callback, db)
        return

    current_time = (
        f"{format_date(consultation.scheduled_at)} · {format_time(consultation.scheduled_at)}"
        if consultation.scheduled_at
        else "уточняется"
    )
    old_slot_id = int(consultation.slot_id or 0)
    consultation_id = int(consultation.id)
    selected_slot_id = int(new_slot.id)
    selected_date = format_date(new_slot.starts_at)
    selected_start = format_time(new_slot.starts_at)
    selected_end = format_time(new_slot.ends_at)
    confirmation_markup = one(
        (
            "✅ Да, перенести консультацию",
            f"consult_reschedule_confirm:{consultation_id}:{old_slot_id}:{selected_slot_id}",
        ),
        ("← Выбрать другое время", retry_action),
        ("Нет, оставить текущее время", "consultation_booked_open"),
    )
    # This screen is read-only. Snapshot the values and close the read
    # transaction before Telegram I/O instead of committing and then reading
    # expired ORM state.
    await db.rollback()
    await _safe_edit(
        callback,
        "⚠️ Подтвердить перенос консультации?\n"
        f"Обращение № {case_number}\n\n"
        f"Текущее время: {current_time}\n"
        f"Новое время: {selected_date} · {selected_start}–{selected_end}\n\n"
        "До подтверждения текущая запись остаётся без изменений. При подтверждении старый слот освободится, а вопрос и документы сохранятся.",
        reply_markup=confirmation_markup,
    )


@router.callback_query(lambda c: c.data.startswith("consult_reschedule_confirm:"))
async def confirm_reschedule_slot(callback: CallbackQuery, db):
    try:
        _, consultation_id_text, old_slot_id_text, new_slot_id_text = callback.data.split(":", 3)
        expected_consultation_id = int(consultation_id_text)
        expected_old_slot_id = int(old_slot_id_text)
        new_slot_id = int(new_slot_id_text)
    except (TypeError, ValueError):
        await _safe_edit(
            callback,
            "Эта кнопка подтверждения переноса устарела. Никаких изменений не выполнено.",
            reply_markup=one(
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    user, case, consultation = await _current_booked_context(callback, db)
    if not case or not consultation:
        await _show_missing_booked_context(callback, db, action="reschedule")
        return
    case_id = int(case.id)
    case_number = str(case.case_number)
    retry_action = bound_case_callback("consult_reschedule", case_id)
    if int(consultation.id) != expected_consultation_id:
        await _safe_edit(
            callback,
            "Эта кнопка относится к предыдущей записи. Текущая консультация не изменена.\n\n"
            f"Обращение № {case_number}\n\n"
            "Откройте актуальную запись и запустите перенос заново — так старая кнопка не сможет изменить новый слот.",
            reply_markup=one(
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("🔄 Начать актуальный перенос", retry_action),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    try:
        consultation, new_slot = await ConsultationService(db).reschedule_booked(
            consultation=consultation,
            case=case,
            client_id=user.id,
            new_slot_id=new_slot_id,
            expected_old_slot_id=expected_old_slot_id,
        )
        new_date = format_date(new_slot.starts_at)
        new_start = format_time(new_slot.starts_at)
        new_end = format_time(new_slot.ends_at)
        await db.commit()
    except (SlotUnavailableError, ValueError) as error:
        await db.rollback()
        try:
            await callback.answer(str(error), show_alert=True)
        except Exception:
            logger.debug("Не удалось показать ошибку переноса callback answer", exc_info=True)
        await consult_reschedule(callback, db)
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось перенести консультацию")
        await _safe_edit(
            callback,
            "Перенос временно не выполнен. Текущая запись сохранена без изменений.\n\n"
            f"Обращение № {case_number}",
            reply_markup=one(
                ("🔄 Повторить перенос", retry_action),
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _safe_edit(
        callback,
        "✅ Консультация перенесена.\n"
        f"Обращение № {case_number}\n\n"
        f"Новая дата: {new_date}\n"
        f"Новое время: {new_start}–{new_end}\n\n"
        "Предыдущий слот освобождён. Вопрос, документы и подтверждение записи сохранены.",
        reply_markup=one(
            ("👨‍⚖ Открыть запись и подготовку", "consultation_booked_open"),
            ("📄 Документы", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_cancel")
async def consult_cancel(callback: CallbackQuery, db):
    _user, case, consultation = await _current_booked_context(callback, db)
    if not case or not consultation:
        await _show_missing_booked_context(callback, db, action="cancel")
        return

    case_number = str(case.case_number)
    consultation_id = int(consultation.id)
    slot_id = int(consultation.slot_id or 0)
    confirmation_markup = one(
        (
            "Да, отменить текущую запись",
            f"consult_cancel_confirm:{consultation_id}:{slot_id}",
        ),
        ("Нет, сохранить запись", "consultation_booked_open"),
        ("💳 Оплаты", "payments_open"),
        ("🏠 Главная", "nav_home"),
    )
    # This is a read-only confirmation screen. Close the transaction before the
    # Telegram network call and use only scalar/markup snapshots afterwards.
    await db.rollback()
    await _safe_edit(
        callback,
        "⚠️ Отменить текущую консультацию?\n"
        f"Обращение № {case_number}\n\n"
        "Слот освободится. Ваш вопрос и загруженные документы останутся в деле, "
        "поэтому новое время можно будет выбрать без повторного заполнения.\n\n"
        "Если по этой записи деньги уже были получены, отмена запустит штатную "
        "проверку возврата. Если фактической оплаты не было, возврат не потребуется. "
        "Финансовый результат будет виден в разделе «Оплаты».",
        reply_markup=confirmation_markup,
    )


@router.callback_query(
    lambda c: c.data == "consult_cancel_confirm"
    or c.data.startswith("consult_cancel_confirm:")
)
async def consult_cancel_confirm(callback: CallbackQuery, db):
    if callback.data == "consult_cancel_confirm":
        await _safe_edit(
            callback,
            "Эта старая кнопка отмены не содержит снимок конкретной записи, поэтому больше не может менять консультацию.\n\n"
            "Ничего не отменено. Откройте текущую запись и подтвердите отмену заново.",
            reply_markup=one(
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    try:
        _, consultation_id_text, slot_id_text = callback.data.split(":", 2)
        expected_consultation_id = int(consultation_id_text)
        expected_slot_id = int(slot_id_text)
    except (TypeError, ValueError):
        await _safe_edit(
            callback,
            "Кнопка отмены повреждена или устарела. Ничего не изменено.",
            reply_markup=one(
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    user, case, consultation = await _current_booked_context(callback, db)
    if not case or not consultation:
        await _show_missing_booked_context(callback, db, action="cancel")
        return
    if (
        int(consultation.id) != expected_consultation_id
        or int(consultation.slot_id or 0) != expected_slot_id
    ):
        await _safe_edit(
            callback,
            "Эта кнопка относится к предыдущей записи или предыдущему слоту. Текущая консультация не отменена.\n\n"
            "Откройте актуальную запись и подтвердите отмену заново.",
            reply_markup=one(
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    case_id = int(case.id)
    case_number = str(case.case_number)
    consultation_id = int(consultation.id)
    try:
        _cancelled, replacement, _case, refund_required = (
            await ConsultationChangeService(db).cancel_and_prepare_rebooking(
                consultation=consultation,
                case=case,
                client_id=user.id,
                comment="Клиент подтвердил отмену консультации в Telegram",
                payments_currently_disabled=payments_disabled(),
            )
        )
        description_ready = consultation_description_ready(replacement)
        refund_required = bool(refund_required)
        await db.commit()
    except ConsultationRefundStateConflict as error:
        await db.rollback()
        logger.warning(
            "Отмена M2 заблокирована из-за конфликта статуса возврата: case_id=%s consultation_id=%s error=%s",
            case_id,
            consultation_id,
            error,
        )
        await _safe_edit(
            callback,
            "⚠️ Возврат требует сверки\n\n"
            f"Обращение № {case_number}\n\n"
            "Автоматический повтор возврата заблокирован, чтобы деньги не были "
            "возвращены дважды. Текущая консультация, слот и статус дела не изменены.\n\n"
            "Откройте «Оплаты» для текущего финансового статуса или напишите команде — "
            "вопрос будет привязан к этому делу.",
            reply_markup=one(
                ("💳 Оплаты", "payments_open"),
                ("✉️ Написать команде", "message_create"),
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except (LookupError, ValueError) as error:
        await db.rollback()
        await _safe_edit(
            callback,
            f"Отмена не выполнена: {error}\n\n"
            f"Обращение № {case_number}\n\n"
            "Текущая запись сохранена без изменений.",
            reply_markup=one(
                ("🔄 Проверить запись", "consultation_booked_open"),
                ("✉️ Написать команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось отменить консультацию")
        await _safe_edit(
            callback,
            "Отмена временно не выполнена. Текущая запись сохранена без изменений.\n\n"
            f"Обращение № {case_number}",
            reply_markup=one(
                (
                    "🔄 Открыть отмену заново",
                    bound_case_callback("consult_cancel", case_id),
                ),
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("✉️ Написать команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    primary = (
        (
            "📅 Выбрать новое время",
            bound_case_callback("consult_booking_start", case_id),
        )
        if description_ready
        else ("📝 Уточнить вопрос", "consult_subject_start")
    )
    if refund_required:
        payment_text = (
            "Заявка на возврат полученной оплаты передана на проверку. "
            "Фактический результат появится в разделе «Оплаты»."
        )
    else:
        payment_text = "По этой записи полученных денег не было, возврат не требуется."

    await _safe_edit(
        callback,
        "✅ Текущая консультация отменена, слот освобождён.\n"
        f"Обращение № {case_number}\n\n"
        "Вопрос и документы сохранены в деле. "
        f"{payment_text}\n\n"
        "Следующий шаг уже подготовлен — можно выбрать новое время или вернуться к делу.",
        reply_markup=one(
            primary,
            ("💳 Оплаты", "payments_open"),
            ("✉️ Написать команде", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )
