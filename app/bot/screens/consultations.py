import logging

from aiogram import Router
from aiogram.types import CallbackQuery

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

router = Router()
logger = logging.getLogger(__name__)


def format_date(value):
    return value.strftime("%d.%m.%Y")


def format_time(value):
    return value.strftime("%H:%M")


def date_buttons(slots, callback_prefix: str):
    buttons = []
    seen = set()
    for slot in slots:
        key = slot.starts_at.date().isoformat()
        if key in seen:
            continue
        seen.add(key)
        buttons.append(
            (f"📅 {format_date(slot.starts_at)}", f"{callback_prefix}:{key}")
        )
    return buttons


def booking_recovery_buttons() -> tuple[tuple[str, str], ...]:
    return (
        ("📅 Выбрать дату и время", "consult_booking_start"),
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
        await _safe_edit(
            callback,
            "Текущая подтверждённая консультация уже изменилась.\n\n"
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
            ("📅 Выбрать дату и время", "consult_booking_start"),
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

    try:
        slots = await SlotService(db).get_available_slots(limit=100)
    except Exception:
        logger.exception("Не удалось загрузить слоты для переноса консультации")
        await _safe_edit(
            callback,
            "Не удалось загрузить свободное время. Текущая запись не изменена.",
            reply_markup=one(
                ("🔄 Повторить", "consult_reschedule"),
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if not slots:
        await _safe_edit(
            callback,
            "Сейчас нет свободного времени для переноса. Текущая запись сохранена.\n\n"
            "Проверьте расписание позже или напишите команде.",
            reply_markup=one(
                ("🔄 Проверить ещё раз", "consult_reschedule"),
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _safe_edit(
        callback,
        "🔄 Перенос консультации\n\n"
        "Выберите новую дату. Текущая запись останется за вами до отдельного "
        "подтверждения нового времени. Повторная оплата не потребуется.",
        reply_markup=one(
            *date_buttons(slots, "consult_reschedule_date"),
            ("← Оставить текущее время", "consultation_booked_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data.startswith("consult_reschedule_date:"))
async def choose_reschedule_date(callback: CallbackQuery, db):
    date_key = callback.data.split(":", 1)[1]
    try:
        slots = await SlotService(db).get_available_slots(limit=100)
    except Exception:
        logger.exception("Не удалось обновить слоты для переноса консультации")
        await _safe_edit(
            callback,
            "Не удалось обновить расписание. Текущая запись не изменена.",
            reply_markup=one(
                ("🔄 Повторить", "consult_reschedule"),
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    selected = [
        slot for slot in slots if slot.starts_at.date().isoformat() == date_key
    ]
    if not selected:
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
    await _safe_edit(
        callback,
        f"🕐 Новое время на {format_date(selected[0].starts_at)}\n\n"
        "Текущая запись ещё не изменена. Выберите слот — следующим экраном я попрошу подтвердить перенос.",
        reply_markup=one(
            *buttons,
            ("← Другие даты", "consult_reschedule"),
            ("Оставить текущее время", "consultation_booked_open"),
            ("🏠 Главная", "nav_home"),
        ),
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
                ("🔄 Выбрать новую дату", "consult_reschedule"),
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    _user, case, consultation = await _current_booked_context(callback, db)
    if not case or not consultation:
        await _show_missing_booked_context(callback, db, action="reschedule")
        return

    try:
        new_slot = await SlotService(db).get_slot(new_slot_id)
    except Exception:
        logger.exception("Не удалось проверить выбранный слот перед переносом")
        await db.rollback()
        await _safe_edit(
            callback,
            "Не удалось проверить выбранное время. Текущая запись не изменена.",
            reply_markup=one(
                ("🔄 Выбрать время заново", "consult_reschedule"),
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

    await db.commit()
    current_time = (
        f"{format_date(consultation.scheduled_at)} · {format_time(consultation.scheduled_at)}"
        if consultation.scheduled_at
        else "уточняется"
    )
    old_slot_id = int(consultation.slot_id or 0)
    await _safe_edit(
        callback,
        "⚠️ Подтвердить перенос консультации?\n\n"
        f"Текущее время: {current_time}\n"
        f"Новое время: {format_date(new_slot.starts_at)} · "
        f"{format_time(new_slot.starts_at)}–{format_time(new_slot.ends_at)}\n\n"
        "До подтверждения текущая запись остаётся без изменений. При подтверждении старый слот освободится, а вопрос и документы сохранятся.",
        reply_markup=one(
            (
                "✅ Да, перенести консультацию",
                f"consult_reschedule_confirm:{consultation.id}:{old_slot_id}:{new_slot.id}",
            ),
            ("← Выбрать другое время", "consult_reschedule"),
            ("Нет, оставить текущее время", "consultation_booked_open"),
        ),
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
                ("🔄 Выбрать время заново", "consult_reschedule"),
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    user, case, consultation = await _current_booked_context(callback, db)
    if not case or not consultation:
        await _show_missing_booked_context(callback, db, action="reschedule")
        return

    try:
        consultation, new_slot = await ConsultationService(db).reschedule_booked(
            consultation=consultation,
            case=case,
            client_id=user.id,
            new_slot_id=new_slot_id,
            expected_old_slot_id=expected_old_slot_id,
        )
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
            "Перенос временно не выполнен. Текущая запись сохранена без изменений.",
            reply_markup=one(
                ("🔄 Повторить перенос", "consult_reschedule"),
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _safe_edit(
        callback,
        "✅ Консультация перенесена.\n\n"
        f"Новая дата: {format_date(new_slot.starts_at)}\n"
        f"Новое время: {format_time(new_slot.starts_at)}–{format_time(new_slot.ends_at)}\n\n"
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

    await _safe_edit(
        callback,
        "⚠️ Отменить текущую консультацию?\n\n"
        "Слот освободится. Ваш вопрос и загруженные документы останутся в деле, "
        "поэтому новое время можно будет выбрать без повторного заполнения.\n\n"
        "Если по этой записи деньги уже были получены, отмена запустит штатную "
        "проверку возврата. Если фактической оплаты не было, возврат не потребуется. "
        "Финансовый результат будет виден в разделе «Оплаты».",
        reply_markup=one(
            ("Да, отменить текущую запись", "consult_cancel_confirm"),
            ("Нет, сохранить запись", "consultation_booked_open"),
            ("💳 Оплаты", "payments_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_cancel_confirm")
async def consult_cancel_confirm(callback: CallbackQuery, db):
    user, case, consultation = await _current_booked_context(callback, db)
    if not case or not consultation:
        await _show_missing_booked_context(callback, db, action="cancel")
        return

    try:
        _cancelled, replacement, case, refund_required = (
            await ConsultationChangeService(db).cancel_and_prepare_rebooking(
                consultation=consultation,
                case=case,
                client_id=user.id,
                comment="Клиент подтвердил отмену консультации в Telegram",
                payments_currently_disabled=payments_disabled(),
            )
        )
        await db.commit()
    except ConsultationRefundStateConflict as error:
        await db.rollback()
        logger.warning(
            "Отмена M2 заблокирована из-за конфликта статуса возврата: case_id=%s consultation_id=%s error=%s",
            case.id,
            consultation.id,
            error,
        )
        await _safe_edit(
            callback,
            "⚠️ Возврат требует сверки\n\n"
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
            f"Отмена не выполнена: {error}\n\nТекущая запись сохранена без изменений.",
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
            "Отмена временно не выполнена. Текущая запись сохранена без изменений.",
            reply_markup=one(
                ("🔄 Повторить отмену", "consult_cancel_confirm"),
                ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    description_ready = consultation_description_ready(replacement)
    primary = (
        ("📅 Выбрать новое время", "consult_booking_start")
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
        "✅ Текущая консультация отменена, слот освобождён.\n\n"
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
