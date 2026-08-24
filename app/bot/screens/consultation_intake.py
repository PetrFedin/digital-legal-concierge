from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from aiogram import Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramServerError,
)
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.case_callback_scope import (
    bound_case_callback,
    callback_matches_action,
    resolve_case_callback_scope,
)
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import ConsultationDescriptionStates
from app.domain.cases.client_case_scope import latest_completed_strict_m2_case_for_user
from app.domain.consultations.consultation_intake import (
    ActiveCaseRouteConflict,
    ConsultationDescriptionRequired,
    ConsultationIntakeError,
    ConsultationIntakeService,
    consultation_description_ready,
    normalized_case_status,
)
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.domain.payments.mode import payments_disabled
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.presentation_time import format_business_datetime, to_business_timezone

router = Router()
logger = logging.getLogger(__name__)

_BOOKING_ENTRY_ACTIONS = ("consult_booking_start", "consult_slot_open")


@dataclass(frozen=True)
class _SlotView:
    id: int
    starts_at: datetime
    ends_at: datetime
    hold_expires_at: datetime | None = None


def _slot_view(slot) -> _SlotView:
    return _SlotView(
        id=int(slot.id),
        starts_at=slot.starts_at,
        ends_at=slot.ends_at,
        hold_expires_at=slot.hold_expires_at,
    )


def _format_date(value) -> str:
    return format_business_datetime(
        value,
        pattern="%d.%m.%Y",
        include_label=False,
    )


def _format_time(value) -> str:
    return format_business_datetime(
        value,
        pattern="%H:%M",
        include_label=False,
    )


def _format_datetime(value) -> str:
    return format_business_datetime(value)


def _business_date_key(value) -> str:
    return to_business_timezone(value).date().isoformat()


def _booking_entry_action(value: str | None) -> str | None:
    for action in _BOOKING_ENTRY_ACTIONS:
        if callback_matches_action(value, action):
            return action
    return None


def _slot_text(slot) -> str:
    return (
        f"{_format_date(slot.starts_at)} · "
        f"{_format_time(slot.starts_at)}–{_format_time(slot.ends_at)}"
    )


def _date_buttons(slots, callback_prefix: str):
    buttons: list[tuple[str, str]] = []
    seen: set[str] = set()
    for slot in slots:
        key = _business_date_key(slot.starts_at)
        if key in seen:
            continue
        seen.add(key)
        buttons.append((f"📅 {_format_date(slot.starts_at)}", f"{callback_prefix}:{key}"))
    return buttons


async def _safe_edit(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    """Render the current consultation step without turning stale Telegram UI into a dead end."""

    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
        return
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            try:
                await callback.answer("Экран уже актуален.")
            except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
                pass
            return
        logger.warning("Не удалось обновить экран консультации: %s", error)
    except (TelegramNetworkError, TelegramServerError) as error:
        logger.warning("Telegram недоступен при обновлении консультации: %s", error)

    try:
        await callback.message.answer(text, reply_markup=reply_markup)
    except (TelegramBadRequest, TelegramNetworkError, TelegramServerError) as error:
        logger.warning("Не удалось открыть экран консультации новым сообщением: %s", error)


async def _active_context(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    return ctx, user, case


async def _show_route_conflict(callback: CallbackQuery, error: Exception) -> None:
    await _safe_edit(
        callback,
        f"Консультация не создана.\n\n{error}",
        reply_markup=one(
            ("✉️ Написать по текущему делу", "message_create"),
            ("🗂 Открыть переписку", "message_history"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


async def _show_description_required(callback: CallbackQuery) -> None:
    await _safe_edit(
        callback,
        "📝 Сначала опишите вопрос\n\n"
        "Чтобы юрист подготовился, сначала сохраните ситуацию и конкретный "
        "вопрос. Затем можно добавить документы и выбрать время.",
        reply_markup=one(
            ("▶️ Описать вопрос", "consult_subject_start"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


async def _show_booking_error(
    callback: CallbackQuery,
    error: Exception,
    *,
    case_id: int | None = None,
) -> None:
    retry = (
        bound_case_callback("consult_booking_start", case_id)
        if case_id
        else "consult_booking_start"
    )
    await _safe_edit(
        callback,
        f"Действие не выполнено: {error}",
        reply_markup=one(
            ("🔄 Продолжить консультацию", retry),
            ("✉️ Задать вопрос команде", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "contact_lawyer")
async def contact_lawyer(callback: CallbackQuery, db, state: FSMContext):
    await state.clear()
    ctx, user, case = await _active_context(callback, db)

    if case and str(case.route or "") != RouteCode.M2.value:
        await callback.message.edit_text(
            "💬 Связаться с юридической командой\n\n"
            "У вас уже есть активное дело. Напишите по нему или откройте "
            "переписку — отдельная консультация не заменит и не скроет текущее дело.",
            reply_markup=one(
                ("✉️ Написать по текущему делу", "message_create"),
                ("🗂 Открыть переписку", "message_history"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if case:
        case_id = int(case.id)
        case_number = str(case.case_number)
        consultation = await ConsultationService(db).get_current_for_case(case.id)
        if consultation and consultation.status == ConsultationStatus.BOOKED:
            primary = ("👨‍⚖ Открыть подтверждённую запись", "consultation_booked_open")
        elif consultation and consultation_description_ready(consultation):
            primary = (
                "📅 Продолжить: выбрать время",
                bound_case_callback("consult_booking_start", case_id),
            )
        else:
            primary = ("📝 Продолжить: описать вопрос", "consult_subject_start")
        await callback.message.edit_text(
            "💬 Юридическая консультация\n\n"
            f"Обращение № {case_number}\n\n"
            "Продолжите сохранённый этап консультации либо напишите команде по обращению.",
            reply_markup=one(
                primary,
                ("✉️ Написать сообщение", "message_create"),
                ("🗂 Открыть переписку", "message_history"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await callback.message.edit_text(
        "💬 Юридическая консультация\n\n"
        "Сначала опишите ситуацию и конкретный вопрос. После этого можно "
        "добавить документы и выбрать свободное время.",
        reply_markup=one(
            ("▶️ Начать: описать вопрос", "consult_subject_start"),
            ("🧮 Рассчитать неустойку", "calc_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_subject_start")
async def subject_start(callback: CallbackQuery, db, state: FSMContext):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        case, _consultation = await ConsultationIntakeService(db).get_or_create_context(user)
        case_id = int(case.id)
        await db.commit()
    except ActiveCaseRouteConflict as error:
        await db.rollback()
        await state.clear()
        await _show_route_conflict(callback, error)
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось начать описание консультации")
        await callback.message.edit_text(
            "Не удалось открыть описание вопроса. Данные текущего дела не изменены.",
            reply_markup=one(
                ("🔄 Повторить", "consult_subject_start"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    cases = list(
        (
            await db.execute(
                select(Case)
                .where(Case.client_id == user.id)
                .where(Case.id != case_id)
                .order_by(Case.created_at.desc(), Case.id.desc())
                .limit(10)
            )
        ).scalars().all()
    )
    buttons = [
        (
            f"📁 {item.case_number}: {(item.title or 'Дело')[:35]}",
            f"consult_subject_case:{item.id}",
        )
        for item in cases
    ]
    buttons.append(("➕ Новая или другая ситуация", "consult_subject_new"))
    await state.set_state(ConsultationDescriptionStates.waiting_subject_choice)
    await _safe_edit(
        callback,
        "📝 Описание консультации\n\n"
        "Выберите существующее дело, к которому относится вопрос, либо "
        "опишите новую ситуацию.",
        reply_markup=one(
            *buttons,
            ("Отменить действие", "nav_cancel"),
            ("📁 Моё дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: c.data.startswith("consult_subject_case:"))
async def subject_existing_case(callback: CallbackQuery, state: FSMContext, db):
    try:
        case_id = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError):
        case_id = 0
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    related_case = (
        await db.execute(
            select(Case)
            .where(Case.id == case_id)
            .where(Case.client_id == user.id)
        )
    ).scalar_one_or_none()
    if not related_case:
        await state.clear()
        await callback.message.edit_text(
            "Выбранное дело больше недоступно. Откройте актуальный список.",
            reply_markup=one(
                ("🔄 Выбрать дело", "consult_subject_start"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await state.update_data(
        subject_type="existing_case",
        related_case_id=related_case.id,
    )
    await state.set_state(ConsultationDescriptionStates.waiting_description)
    await callback.message.edit_text(
        f"📝 Вопрос по делу {related_case.case_number}\n\n"
        "Напишите конкретные вопросы, сомнения или новые обстоятельства. "
        "Минимум 20 символов.",
        reply_markup=one(
            ("Выбрать другое дело", "consult_subject_start"),
            ("Отменить действие", "nav_cancel"),
            ("📁 Моё дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_subject_new")
async def subject_new_case(callback: CallbackQuery, state: FSMContext):
    await state.update_data(subject_type="new_or_other", related_case_id=None)
    await state.set_state(ConsultationDescriptionStates.waiting_description)
    await callback.message.edit_text(
        "📝 Опишите ситуацию и конкретный вопрос для юриста.\n\n"
        "Минимум 20 символов. Текст сохранится только после успешной записи в дело.",
        reply_markup=one(
            ("Выбрать существующее дело", "consult_subject_start"),
            ("Отменить действие", "nav_cancel"),
            ("📁 Моё дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_description_start")
async def legacy_description_start(callback: CallbackQuery, db, state: FSMContext):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        await ConsultationIntakeService(db).get_or_create_context(user)
        await db.commit()
    except ActiveCaseRouteConflict as error:
        await db.rollback()
        await state.clear()
        await _show_route_conflict(callback, error)
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось восстановить начало консультации")
        await callback.message.edit_text(
            "Не удалось начать описание. Текущее дело не изменено.",
            reply_markup=one(
                ("🔄 Повторить", "consult_description_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await state.update_data(subject_type="new_or_other", related_case_id=None)
    await state.set_state(ConsultationDescriptionStates.waiting_description)
    await _safe_edit(
        callback,
        "📝 Опишите ситуацию и конкретный вопрос для юриста.\n\n"
        "После сохранения можно добавить документы и выбрать время.",
        reply_markup=one(
            ("Отменить действие", "nav_cancel"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.message(ConsultationDescriptionStates.waiting_description)
async def save_description(message: Message, state: FSMContext, db):
    text = (message.text or "").strip()
    if len(text) < 20:
        await message.answer(
            "Опишите вопрос подробнее — минимум 20 символов. Введённый текст можно дополнить.",
            reply_markup=one(
                ("Отменить действие", "nav_cancel"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
        return
    if len(text) > 4000:
        await message.answer(
            "Сократите описание до 4000 символов. Текст пока не сохранён.",
            reply_markup=one(
                ("Отменить действие", "nav_cancel"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
        return

    data = await state.get_data()
    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    try:
        case, consultation, was_booked = await ConsultationIntakeService(db).save_description(
            client=user,
            description=text,
            subject_type=data.get("subject_type", "new_or_other"),
            related_case_id=data.get("related_case_id"),
        )
        case_id = int(case.id)
        consultation_status = consultation.status
        await db.commit()
    except ActiveCaseRouteConflict as error:
        await db.rollback()
        await state.clear()
        await message.answer(
            f"Вопрос не сохранён. {error}",
            reply_markup=one(
                ("✉️ Написать по текущему делу", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except ValueError as error:
        await db.rollback()
        await state.clear()
        await message.answer(
            f"Вопрос не сохранён: {error}",
            reply_markup=one(
                ("🔄 Выбрать тему заново", "consult_subject_start"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось сохранить описание консультации")
        await message.answer(
            "Вопрос временно не сохранён. Текст не потерян в чате: отправьте его повторно.",
            reply_markup=one(
                ("🔄 Начать ввод заново", "consult_subject_start"),
                ("✉️ Написать команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
        return

    await state.clear()
    if was_booked or consultation_status == ConsultationStatus.BOOKED:
        await message.answer(
            "✅ Вопрос обновлён. Дата и время консультации сохранены.\n\n"
            "Юрист увидит актуальное описание до встречи.",
            reply_markup=one(
                ("👨‍⚖ Открыть запись", "consultation_booked_open"),
                ("📄 Добавить документы", "documents_open"),
                ("✉️ Задать вопрос команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
        return

    await message.answer(
        "✅ Вопрос сохранён.\n\n"
        "Следующий шаг: при необходимости добавьте документы. Если документов "
        "нет, переходите к выбору времени без потери описания.",
        reply_markup=one(
            ("📄 Добавить документы", "documents_open"),
            ("Продолжить без документов", f"doc_skip_m2:v2:{case_id}"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


async def _prepare_slots(callback: CallbackQuery, db, *, scope=None):
    if scope is None:
        ctx = BotContextService(db)
        user = await ctx.get_user_from_callback(callback)
    else:
        user = scope.user
    case_id_hint = int(scope.case.id) if scope is not None and scope.case is not None else None
    try:
        case, consultation = await ConsultationIntakeService(db).prepare_slot_selection(
            client=user
        )
        slots = await SlotService(db).get_available_slots(limit=60)
        case_id = int(case.id)
        consultation_id = int(consultation.id)
        slot_views = [_slot_view(slot) for slot in slots]
        await db.commit()
        return case_id, consultation_id, slot_views
    except ConsultationDescriptionRequired:
        # Keep a newly created M2 draft so /start and My Case can restore it.
        await db.commit()
        await _show_description_required(callback)
        return None
    except ActiveCaseRouteConflict as error:
        await db.rollback()
        await _show_route_conflict(callback, error)
        return None
    except ConsultationIntakeError as error:
        await db.rollback()
        await _show_booking_error(callback, error, case_id=case_id_hint)
        return None
    except Exception:
        await db.rollback()
        logger.exception("Не удалось подготовить выбор времени консультации")
        retry = (
            bound_case_callback("consult_booking_start", case_id_hint)
            if case_id_hint
            else "consult_booking_start"
        )
        await callback.message.edit_text(
            "Не удалось загрузить доступное время. Данные вопроса и документов сохранены.",
            reply_markup=one(
                ("🔄 Повторить", retry),
                ("✉️ Написать команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return None


@router.callback_query(lambda c: _booking_entry_action(c.data) is not None)
async def booking_start(callback: CallbackQuery, db):
    action = _booking_entry_action(callback.data)
    scope = None
    if action is not None:
        scope = await resolve_case_callback_scope(
            callback,
            db,
            action=action,
            allow_legacy_message_case_context=True,
        )
        if scope is None:
            return
        if scope.case is None or str(scope.case.route or "").upper() != RouteCode.M2.value:
            await db.rollback()
            await _safe_edit(
                callback,
                "Выбор времени доступен только внутри текущего консультационного обращения. Ничего не изменено.",
                reply_markup=one(
                    ("📁 Выбрать обращение", "my_cases_open"),
                    ("✉️ Написать команде", "message_create"),
                    ("🏠 Главная", "nav_home"),
                ),
            )
            return

    # Internal recovery calls originate only from already validated date/slot
    # handlers. They deliberately reuse the current selected M2 context instead
    # of treating consult_date:/consult_slot_select: as a fresh entry callback.
    prepared = await _prepare_slots(callback, db, scope=scope)
    if prepared is None:
        return
    case_id, _consultation_id, slots = prepared
    retry = bound_case_callback("consult_booking_start", case_id)
    if not slots:
        await _safe_edit(
            callback,
            "Сейчас свободных слотов нет. Вопрос и документы сохранены.\n\n"
            "Повторите позже или напишите юридической команде.",
            reply_markup=one(
                ("🔄 Проверить свободное время", retry),
                ("✉️ Написать команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await _safe_edit(
        callback,
        "📅 Выберите дату консультации.\n\n"
        "Вопрос уже сохранён. Выбор даты не изменяет документы и описание.",
        reply_markup=one(
            *_date_buttons(slots, "consult_date"),
            ("📄 Документы", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data.startswith("consult_date:"))
async def choose_date(callback: CallbackQuery, db):
    date_key = callback.data.split(":", 1)[1]
    prepared = await _prepare_slots(callback, db)
    if prepared is None:
        return
    case_id, _consultation_id, slots = prepared
    selected = [
        slot for slot in slots if _business_date_key(slot.starts_at) == date_key
    ]
    if not selected:
        await callback.answer("На эту дату свободное время уже закончилось.", show_alert=True)
        await booking_start(callback, db)
        return
    buttons = [
        (
            f"{_format_time(slot.starts_at)}–{_format_time(slot.ends_at)}",
            f"consult_slot_select:{slot.id}",
        )
        for slot in selected
    ]
    await _safe_edit(
        callback,
        f"🕐 Выберите время на {_format_date(selected[0].starts_at)}.",
        reply_markup=one(
            *buttons,
            (
                "← Другие даты",
                bound_case_callback("consult_booking_start", case_id),
            ),
            ("📄 Документы", "documents_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


async def _show_booked(callback: CallbackQuery, slot: _SlotView) -> None:
    await _safe_edit(
        callback,
        "✅ Консультация подтверждена.\n\n"
        f"Дата и время: {_slot_text(slot)}\n"
        "Вопрос: сохранён\n"
        "Документы: можно добавить или обновить до встречи\n\n"
        + (
            "Онлайн-оплата не требуется."
            if payments_disabled()
            else "Оплата подтверждена."
        ),
        reply_markup=one(
            ("👨‍⚖ Открыть запись и подготовку", "consultation_booked_open"),
            ("📄 Добавить документы", "documents_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data.startswith("consult_slot_select:"))
async def choose_slot(callback: CallbackQuery, db):
    try:
        slot_id = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError):
        await callback.message.edit_text(
            "Эта кнопка выбора времени больше не актуальна.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    intake = ConsultationIntakeService(db)
    try:
        case, consultation, slot = await intake.reserve_slot(
            client=user,
            slot_id=slot_id,
            payment_required=not payments_disabled(),
        )
        case_id = int(case.id)
        if payments_disabled():
            consultation, slot = await intake.confirm_without_payment(
                client=user,
                case=case,
                consultation=consultation,
            )
        slot_view = _slot_view(slot)
        await db.commit()
    except ConsultationDescriptionRequired:
        await db.rollback()
        await _show_description_required(callback)
        return
    except ActiveCaseRouteConflict as error:
        await db.rollback()
        await _show_route_conflict(callback, error)
        return
    except (ConsultationIntakeError, SlotUnavailableError, ValueError) as error:
        await db.rollback()
        await callback.answer(str(error), show_alert=True)
        await booking_start(callback, db)
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось зарезервировать консультацию")
        await callback.message.edit_text(
            "Не удалось сохранить выбранное время. Вопрос и документы не изменены.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if payments_disabled():
        await _show_booked(callback, slot_view)
        return

    hold_until = (
        _format_datetime(slot_view.hold_expires_at)
        if slot_view.hold_expires_at
        else "в течение 10 минут"
    )
    await _safe_edit(
        callback,
        "✅ Время временно удерживается за вами.\n\n"
        f"Дата: {_format_date(slot_view.starts_at)}\n"
        f"Время: {_format_time(slot_view.starts_at)}–{_format_time(slot_view.ends_at)}\n"
        f"Резерв до: {hold_until}\n\n"
        "Подтвердите запись оплатой до окончания резерва. Вопрос и документы уже сохранены.",
        reply_markup=one(
            (
                "💳 Оплатить и подтвердить",
                bound_case_callback("consult_pay", case_id),
            ),
            (
                "Выбрать другое время",
                bound_case_callback("consult_booking_start", case_id),
            ),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: payments_disabled() and c.data == "consult_pay")
async def stale_consult_pay(callback: CallbackQuery, db):
    ctx, user, case = await _active_context(callback, db)
    if not case:
        completed_m2 = await latest_completed_strict_m2_case_for_user(db, user_id=user.id)
        completed_case_number = str(completed_m2.case_number) if completed_m2 else None
        await db.rollback()
        if completed_case_number:
            await _safe_edit(
                callback,
                "🔒 КОНСУЛЬТАЦИЯ УЖЕ ЗАВЕРШЕНА\n\n"
                f"Дело {completed_case_number} находится в архиве. "
                "Старая кнопка подтверждения оплаты не создаёт новую запись и не меняет закрытое обращение.\n\n"
                "Откройте итог консультации или нужный раздел архива.",
                reply_markup=one(
                    ("👨‍⚖ Итог консультации", "consultation_result_open"),
                    ("💳 Оплаты", "payments_open"),
                    ("💬 Архив переписки", "message_history"),
                    ("🕘 История дела", "case_history_open"),
                    ("📁 Моё дело", "my_case_open"),
                    ("🏠 Главная", "nav_home"),
                ),
            )
            return
        await _safe_edit(
            callback,
            "👨‍⚖ Активная консультация не найдена.\n\n"
            "Эта старая кнопка ничего не изменила. Новую консультацию можно начать отдельным действием.",
            reply_markup=one(
                ("💬 Юридическая помощь", "contact_lawyer"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    case_id = int(case.id)
    case_number = str(case.case_number)
    if str(case.route or "") != RouteCode.M2.value:
        await db.rollback()
        await _safe_edit(
            callback,
            "У вас уже есть активное дело по другому маршруту. Старая кнопка консультации ничего не изменила.",
            reply_markup=one(
                ("✉️ Написать по текущему делу", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    consultation = await ConsultationService(db).get_current_for_case(case.id)
    if not consultation:
        await db.rollback()
        await callback.message.edit_text(
            "Активная запись не найдена. Описание дела сохранено в карточке.\n\n"
            f"Обращение № {case_number}",
            reply_markup=one(
                (
                    "📅 Выбрать время",
                    bound_case_callback("consult_booking_start", case_id),
                ),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    try:
        _consultation, slot = await ConsultationIntakeService(db).confirm_without_payment(
            client=user,
            case=case,
            consultation=consultation,
        )
        slot_view = _slot_view(slot)
        await db.commit()
    except ConsultationDescriptionRequired:
        await db.rollback()
        await _show_description_required(callback)
        return
    except (ConsultationIntakeError, SlotUnavailableError, ValueError) as error:
        await db.rollback()
        await _show_booking_error(callback, error, case_id=case_id)
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось завершить устаревшее подтверждение консультации")
        await callback.message.edit_text(
            "Запись временно не подтверждена. Вопрос и выбранные данные сохранены.",
            reply_markup=one(
                (
                    "🔄 Повторить подтверждение",
                    bound_case_callback("consult_pay", case_id),
                ),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await _show_booked(callback, slot_view)
