from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.screens import messages
from app.domain.cases.client_case_scope import CLIENT_COMPLETED_CASE_STATUSES
from app.models.case import Case

router = Router()
_COMPLETED = {str(value) for value in CLIENT_COMPLETED_CASE_STATUSES}
_PREFIX = "message_retarget_current:v2:"


def _parse_case_id(value: str) -> int | None:
    if not str(value or "").startswith(_PREFIX):
        return None
    try:
        case_id = int(str(value)[len(_PREFIX) :])
    except (TypeError, ValueError):
        return None
    return case_id if case_id > 0 else None


@router.callback_query(lambda c: str(c.data or "").startswith(_PREFIX))
async def retarget_preserved_draft_to_current_case(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    """Retarget a saved draft only after an explicit exact-case confirmation.

    This action never sends the message. It only updates the draft target and
    returns the client to the normal review screen, where `message_submit` locks
    the same case again before the actual write.
    """

    expected_case_id = _parse_case_id(str(callback.data or ""))
    data = await state.get_data()
    draft = str(data.get("draft_text") or "").strip()
    source_message_id = data.get("source_message_id")
    if expected_case_id is None or not draft or source_message_id is None:
        await callback.message.edit_text(
            "Этот recovery-экран больше не содержит активного черновика. Ничего не отправлено.",
            reply_markup=one(
                ("✉️ Новый вопрос", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = (
        await db.execute(
            select(Case)
            .where(
                Case.id == int(expected_case_id),
                Case.client_id == int(user.id),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    active = await ctx.case_service.get_active_case_for_user(user.id)
    if (
        case is None
        or str(case.status) in _COMPLETED
        or active is None
        or int(active.id) != int(expected_case_id)
    ):
        await db.rollback()
        await callback.message.edit_text(
            "Пока вы проверяли черновик, активное дело снова изменилось. Черновик сохранён и никуда не отправлен.",
            reply_markup=one(
                ("↩️ Вернуться к черновику", "message_review_return"),
                ("📁 Моё дело", "my_case_open"),
                ("✖️ Отменить черновик", "message_discard_confirm"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    # rollback() expires ORM state even with expire_on_commit=False. Snapshot all
    # values needed by FSM/presentation before releasing the read transaction.
    case_id = int(case.id)
    case_number = str(case.case_number)
    await db.rollback()
    await state.update_data(
        case_id=case_id,
        case_number=case_number,
        new_request_confirmed=False,
        client_message_case_id=case_id,
        client_message_recovery_case_id=None,
    )
    await state.set_state(messages.MessageStates.confirming_message)
    data = await state.get_data()
    await callback.message.edit_text(
        "✅ Черновик перенесён в актуальное дело. Ничего ещё не отправлено.\n\n"
        + messages._draft_review_text(data),
        reply_markup=messages._review_markup(),
    )
    await callback.answer("Проверьте текст и отдельно подтвердите отправку.")


__all__ = ["router"]
