from __future__ import annotations

from aiogram import Router
from aiogram.filters import Filter
from aiogram.types import CallbackQuery

from app.bot.client_case_view import client_action_for
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.cases.case_timeline import get_client_visible_status
from app.domain.cases.client_case_scope import latest_completed_case_for_user
from app.domain.statuses.case_statuses import CaseStatus, RouteCode

router = Router()


async def _context(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    return ctx, user, case


def _status(case) -> str:
    return str(getattr(case, "status", "") or "")


def _route(case) -> str:
    return str(getattr(case, "route", "") or "")


class StalePoaInstructionFilter(Filter):
    async def __call__(self, callback: CallbackQuery, db) -> bool:
        if callback.data != "poa_instruction":
            return False
        _ctx, _user, case = await _context(callback, db)
        return not bool(
            case
            and _route(case) == RouteCode.M1.value
            and _status(case) == CaseStatus.M1_POWER_OF_ATTORNEY.value
        )


class StaleCourtStatusFilter(Filter):
    async def __call__(self, callback: CallbackQuery, db) -> bool:
        if callback.data != "court_status":
            return False
        _ctx, _user, case = await _context(callback, db)
        return not bool(
            case
            and _route(case) == RouteCode.M1.value
            and _status(case)
            in {
                CaseStatus.M1_WAITING_30_DAYS.value,
                CaseStatus.M1_COURT_STAGE.value,
            }
        )


def _active_buttons(case) -> list[tuple[str, str]]:
    action = client_action_for(case)
    buttons: list[tuple[str, str]] = []
    if action and action.callback not in {"poa_instruction", "court_status"}:
        buttons.append((f"▶️ {action.label}", action.callback))
    buttons.append(("📁 Моё дело", "my_case_open"))
    if action is None or action.callback != "message_create":
        buttons.append(("✉️ Написать команде", "message_create"))
    buttons.append(("🏠 Главная", "nav_home"))
    return buttons


async def _render_stale(callback: CallbackQuery, db, *, old_stage: str) -> None:
    _ctx, user, case = await _context(callback, db)
    if case is not None:
        current = get_client_visible_status(_status(case))
        action = client_action_for(case)
        next_text = action.description if action else str(case.next_action or "Откройте актуальную карточку дела.")
        await callback.message.edit_text(
            f"↩️ Эта кнопка относится к предыдущему этапу: {old_stage}.\n\n"
            f"Сейчас по делу: {current}.\n\n"
            f"ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n{next_text}\n\n"
            "Старое сообщение ничего не изменило. Продолжайте только из актуальной карточки.",
            reply_markup=one(*_active_buttons(case)),
        )
        return

    completed = await latest_completed_case_for_user(db, user_id=user.id)
    if completed is not None:
        await callback.message.edit_text(
            f"🔒 Этап «{old_stage}» уже завершён. Старая кнопка ничего не меняет.\n\n"
            f"Последнее обращение {completed.case_number} доступно только для просмотра.",
            reply_markup=one(
                ("📁 Архив обращения", "my_case_open"),
                ("📄 Документы", "documents_open"),
                ("💳 Оплаты", "payments_open"),
                ("🕘 История", "case_history_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await callback.message.edit_text(
        f"Этап «{old_stage}» больше не относится к активному обращению. Ничего не изменено.",
        reply_markup=one(
            ("🏠 Главная", "nav_home"),
            ("🧮 Новое обращение", "calc_start"),
        ),
    )


@router.callback_query(StalePoaInstructionFilter())
async def stale_poa_instruction(callback: CallbackQuery, db):
    await _render_stale(callback, db, old_stage="оформление доверенности")


@router.callback_query(StaleCourtStatusFilter())
async def stale_court_status(callback: CallbackQuery, db):
    await _render_stale(callback, db, old_stage="судебный контроль")
