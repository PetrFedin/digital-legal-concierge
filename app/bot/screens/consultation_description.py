from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import ConsultationDescriptionStates
from app.domain.consultations.consultation_intake import (
    ActiveCaseRouteConflict,
    ConsultationIntakeService,
)
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case

router = Router()
logger = logging.getLogger(__name__)


DESCRIPTION_MIN_LENGTH = 20
DESCRIPTION_MAX_LENGTH = 4000
DESCRIPTION_PREVIEW_LENGTH = 2600


def _description_preview(text: str) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= DESCRIPTION_PREVIEW_LENGTH:
        return normalized
    return normalized[:DESCRIPTION_PREVIEW_LENGTH].rstrip() + "\n…\n(полный текст сохранён в черновике)"


def _valid_draft(data: dict) -> str | None:
    draft = str(data.get("description_draft") or "").strip()
    if DESCRIPTION_MIN_LENGTH <= len(draft) <= DESCRIPTION_MAX_LENGTH:
        return draft
    return None


def _subject_ready(data: dict) -> bool:
    subject_type = data.get("subject_type")
    if subject_type == "new_or_other":
        return True
    if subject_type != "existing_case":
        return False
    try:
        return int(data.get("related_case_id")) > 0
    except (TypeError, ValueError):
        return False


def _review_markup():
    return one(
        ("✅ Сохранить вопрос", "consult_description_confirm"),
        ("✏️ Изменить текст", "consult_description_edit"),
        ("← Изменить привязку", "consult_subject_start"),
        ("Отменить действие", "nav_cancel"),
        ("📁 Моё дело", "my_case_open"),
    )


def _entry_markup(*, has_draft: bool):
    buttons: list[tuple[str, str]] = []
    if has_draft:
        buttons.append(("✅ Проверить сохранённый черновик", "consult_description_review"))
    buttons.extend(
        [
            ("← Изменить привязку", "consult_subject_start"),
            ("Отменить действие", "nav_cancel"),
            ("📁 Моё дело", "my_case_open"),
        ]
    )
    return one(*buttons)


async def _safe_edit(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
        await callback.answer("Экран уже актуален.")


async def _present_committed_description(
    callback: CallbackQuery,
    *,
    booked: bool,
) -> None:
    if booked:
        text = (
            "✅ Вопрос обновлён. Дата и время консультации сохранены.\n\n"
            "Юрист увидит актуальное описание до встречи."
        )
        reply_markup = one(
            ("👨‍⚖ Открыть запись", "consultation_booked_open"),
            ("📄 Добавить документы", "documents_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    else:
        text = (
            "✅ Вопрос сохранён.\n\n"
            "Следующий шаг: при необходимости добавьте документы. Если документов "
            "нет, переходите к выбору времени без потери описания."
        )
        reply_markup = one(
            ("📄 Добавить документы", "documents_open"),
            ("Продолжить без документов", "doc_skip_m2"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )

    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            await callback.answer("Вопрос уже сохранён.")
            return
        logger.warning("Не удалось обновить сообщение после сохранения вопроса: %s", error)
        try:
            await callback.message.answer(text, reply_markup=reply_markup)
        except TelegramBadRequest:
            logger.exception("Не удалось показать сохранённый вопрос новым сообщением")
            await callback.answer(
                "Вопрос сохранён. Откройте «Моё дело» для продолжения.",
                show_alert=True,
            )
            return
        await callback.answer("Вопрос сохранён. Результат открыт новым сообщением.")


async def _reset_to_subject_choice(
    state: FSMContext,
    *,
    draft: str | None,
) -> None:
    await state.clear()
    if draft:
        await state.update_data(description_draft=draft)
    await state.set_state(ConsultationDescriptionStates.waiting_subject_choice)


@router.callback_query(lambda c: c.data == "consult_subject_start")
async def subject_start(callback: CallbackQuery, db, state: FSMContext):
    previous = await state.get_data()
    draft = _valid_draft(previous)
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        case, _consultation = await ConsultationIntakeService(db).get_or_create_context(user)
        cases = list(
            (
                await db.execute(
                    select(Case)
                    .where(Case.client_id == user.id)
                    .where(Case.id != case.id)
                    .order_by(Case.created_at.desc(), Case.id.desc())
                    .limit(10)
                )
            ).scalars().all()
        )
        await db.commit()
    except ActiveCaseRouteConflict as error:
        await db.rollback()
        await state.clear()
        draft_note = (
            f"\n\nЧерновик из этого шага:\n{_description_preview(draft)}"
            if draft
            else ""
        )
        await _safe_edit(
            callback,
            f"Консультация не создана.\n\n{error}{draft_note}",
            reply_markup=one(
                ("✉️ Написать по текущему делу", "message_create"),
                ("🗂 Открыть переписку", "message_history"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось открыть выбор темы консультации")
        await _safe_edit(
            callback,
            "Не удалось открыть выбор темы. Уже введённый черновик не удалён.",
            reply_markup=one(
                ("🔄 Повторить", "consult_subject_start"),
                ("✉️ Написать команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _reset_to_subject_choice(state, draft=draft)
    buttons = [
        (
            f"📁 {item.case_number}: {(item.title or 'Дело')[:35]}",
            f"consult_subject_case:{item.id}",
        )
        for item in cases
    ]
    buttons.append(("➕ Новая или другая ситуация", "consult_subject_new"))
    draft_text = (
        "\n\nВаш ранее введённый текст сохранён. После выбора привязки его можно проверить перед записью."
        if draft
        else ""
    )
    await _safe_edit(
        callback,
        "📝 Вопрос для консультации · шаг 1 из 3\n\n"
        "Выберите существующее дело, к которому относится вопрос, либо "
        f"новую/другую ситуацию.{draft_text}",
        reply_markup=one(
            *buttons,
            ("Отменить действие", "nav_cancel"),
            ("📁 Моё дело", "my_case_open"),
        ),
    )


@router.message(ConsultationDescriptionStates.waiting_subject_choice)
async def subject_choice_requires_button(message: Message, state: FSMContext):
    draft = _valid_draft(await state.get_data())
    await message.answer(
        "На этом шаге выберите привязку кнопкой в предыдущем сообщении. "
        + ("Черновик текста сохранён." if draft else "После этого я попрошу текст вопроса."),
        reply_markup=one(
            ("🔄 Показать выбор ещё раз", "consult_subject_start"),
            ("Отменить действие", "nav_cancel"),
        ),
    )


@router.callback_query(lambda c: c.data.startswith("consult_subject_case:"))
async def subject_existing_case(callback: CallbackQuery, state: FSMContext, db):
    data = await state.get_data()
    draft = _valid_draft(data)
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
        await _reset_to_subject_choice(state, draft=draft)
        await _safe_edit(
            callback,
            "Выбранное дело больше недоступно. Черновик текста сохранён — выберите актуальную привязку.",
            reply_markup=one(
                ("🔄 Открыть актуальный список", "consult_subject_start"),
                ("Отменить действие", "nav_cancel"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await state.update_data(
        subject_type="existing_case",
        related_case_id=related_case.id,
    )
    await state.set_state(ConsultationDescriptionStates.waiting_description)
    draft_text = (
        f"\n\nСохранённый черновик:\n{_description_preview(draft)}"
        if draft
        else ""
    )
    await _safe_edit(
        callback,
        f"📝 Вопрос для консультации · шаг 2 из 3\n\n"
        f"Привязка: дело {related_case.case_number}.\n"
        "Отправьте конкретные вопросы, сомнения или новые обстоятельства. "
        f"Минимум {DESCRIPTION_MIN_LENGTH} символов.{draft_text}",
        reply_markup=_entry_markup(has_draft=bool(draft)),
    )


@router.callback_query(lambda c: c.data == "consult_subject_new")
async def subject_new_case(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    draft = _valid_draft(data)
    await state.update_data(subject_type="new_or_other", related_case_id=None)
    await state.set_state(ConsultationDescriptionStates.waiting_description)
    draft_text = (
        f"\n\nСохранённый черновик:\n{_description_preview(draft)}"
        if draft
        else ""
    )
    await _safe_edit(
        callback,
        "📝 Вопрос для консультации · шаг 2 из 3\n\n"
        "Привязка: новая или другая ситуация.\n"
        f"Опишите ситуацию и конкретный вопрос для юриста, минимум {DESCRIPTION_MIN_LENGTH} символов."
        f"{draft_text}",
        reply_markup=_entry_markup(has_draft=bool(draft)),
    )


@router.callback_query(lambda c: c.data == "consult_description_start")
async def legacy_description_start(callback: CallbackQuery, db, state: FSMContext):
    await subject_start(callback, db, state)


@router.message(ConsultationDescriptionStates.waiting_description)
async def capture_description(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if len(text) < DESCRIPTION_MIN_LENGTH:
        await message.answer(
            f"Опишите вопрос подробнее — минимум {DESCRIPTION_MIN_LENGTH} символов. "
            "Черновик не записан в дело, пока вы явно не подтвердите его.",
            reply_markup=one(
                ("← Изменить привязку", "consult_subject_start"),
                ("Отменить действие", "nav_cancel"),
            ),
        )
        return
    if len(text) > DESCRIPTION_MAX_LENGTH:
        await message.answer(
            f"Сократите описание до {DESCRIPTION_MAX_LENGTH} символов. "
            "Предыдущий сохранённый черновик не удалён.",
            reply_markup=one(
                ("✅ Проверить предыдущий черновик", "consult_description_review"),
                ("Отменить действие", "nav_cancel"),
            ),
        )
        return

    data = await state.get_data()
    await state.update_data(description_draft=text)
    if not _subject_ready(data):
        await _reset_to_subject_choice(state, draft=text)
        await message.answer(
            "Текст сохранён как черновик, но привязка вопроса устарела. "
            "Выберите её заново — повторно вводить текст не нужно.",
            reply_markup=one(
                ("▶️ Выбрать привязку", "consult_subject_start"),
                ("Отменить действие", "nav_cancel"),
            ),
        )
        return

    await state.set_state(ConsultationDescriptionStates.reviewing_description)
    await message.answer(
        "📝 Вопрос для консультации · шаг 3 из 3\n\n"
        "Проверьте текст перед сохранением в дело:\n\n"
        f"{_description_preview(text)}\n\n"
        "Нажмите «Сохранить вопрос» только когда всё верно.",
        reply_markup=_review_markup(),
    )


@router.callback_query(lambda c: c.data == "consult_description_review")
async def review_description(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    draft = _valid_draft(data)
    if not draft:
        await state.set_state(ConsultationDescriptionStates.waiting_description)
        await _safe_edit(
            callback,
            "Черновик текста больше недоступен. Отправьте вопрос ещё раз — в дело ничего не записано.",
            reply_markup=one(
                ("← Изменить привязку", "consult_subject_start"),
                ("Отменить действие", "nav_cancel"),
            ),
        )
        return
    if not _subject_ready(data):
        await _reset_to_subject_choice(state, draft=draft)
        await _safe_edit(
            callback,
            "Черновик сохранён, но привязку нужно выбрать заново.",
            reply_markup=one(
                ("▶️ Выбрать привязку", "consult_subject_start"),
                ("Отменить действие", "nav_cancel"),
            ),
        )
        return

    await state.set_state(ConsultationDescriptionStates.reviewing_description)
    await _safe_edit(
        callback,
        "📝 Вопрос для консультации · шаг 3 из 3\n\n"
        "Проверьте текст перед сохранением в дело:\n\n"
        f"{_description_preview(draft)}\n\n"
        "Нажмите «Сохранить вопрос» только когда всё верно.",
        reply_markup=_review_markup(),
    )


@router.callback_query(lambda c: c.data == "consult_description_edit")
async def edit_description(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    draft = _valid_draft(data)
    if not draft:
        await state.set_state(ConsultationDescriptionStates.waiting_description)
        await _safe_edit(
            callback,
            "Отправьте новый текст вопроса. Сохранение произойдёт только после отдельного подтверждения.",
            reply_markup=one(
                ("← Изменить привязку", "consult_subject_start"),
                ("Отменить действие", "nav_cancel"),
            ),
        )
        return

    await state.set_state(ConsultationDescriptionStates.waiting_description)
    await _safe_edit(
        callback,
        "✏️ Изменение вопроса\n\n"
        "Текущий черновик сохранён до тех пор, пока вы не отправите замену:\n\n"
        f"{_description_preview(draft)}\n\n"
        "Отправьте новый текст или вернитесь к проверке текущего.",
        reply_markup=one(
            ("✅ Вернуться к проверке", "consult_description_review"),
            ("← Изменить привязку", "consult_subject_start"),
            ("Отменить действие", "nav_cancel"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_description_confirm")
async def confirm_description(callback: CallbackQuery, state: FSMContext, db):
    data = await state.get_data()
    draft = _valid_draft(data)
    if not draft or not _subject_ready(data):
        await _reset_to_subject_choice(state, draft=draft)
        await _safe_edit(
            callback,
            "Эта кнопка подтверждения больше не соответствует текущему черновику. "
            "Сохранённый текст не потерян — восстановите привязку и проверьте его ещё раз.",
            reply_markup=one(
                ("▶️ Восстановить шаг", "consult_subject_start"),
                ("Отменить действие", "nav_cancel"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        _case, consultation, was_booked = await ConsultationIntakeService(db).save_description(
            client=user,
            description=draft,
            subject_type=str(data["subject_type"]),
            related_case_id=data.get("related_case_id"),
        )
        await db.commit()
    except ActiveCaseRouteConflict as error:
        await db.rollback()
        await state.clear()
        await _safe_edit(
            callback,
            f"Вопрос не сохранён: {error}\n\n"
            "Черновик остаётся видимым выше в чате, но этот маршрут больше нельзя продолжать.",
            reply_markup=one(
                ("✉️ Написать по текущему делу", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except ValueError as error:
        await db.rollback()
        await _reset_to_subject_choice(state, draft=draft)
        await _safe_edit(
            callback,
            f"Вопрос пока не сохранён: {error}\n\n"
            "Текст черновика сохранён. Выберите актуальную привязку и подтвердите его повторно.",
            reply_markup=one(
                ("🔄 Выбрать привязку", "consult_subject_start"),
                ("Отменить действие", "nav_cancel"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось сохранить подтверждённое описание консультации")
        await state.set_state(ConsultationDescriptionStates.reviewing_description)
        await _safe_edit(
            callback,
            "Вопрос временно не сохранён. Черновик остался на шаге проверки — повторно вводить текст не нужно.",
            reply_markup=one(
                ("🔄 Повторить сохранение", "consult_description_confirm"),
                ("✏️ Изменить текст", "consult_description_edit"),
                ("← Изменить привязку", "consult_subject_start"),
                ("✉️ Написать команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    booked = was_booked or consultation.status == ConsultationStatus.BOOKED
    try:
        await state.clear()
    except Exception:
        logger.exception("Вопрос сохранён, но не удалось очистить Telegram FSM")
    await _present_committed_description(callback, booked=booked)
