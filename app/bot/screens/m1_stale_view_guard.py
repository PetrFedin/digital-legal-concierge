from __future__ import annotations

from aiogram import Router
from aiogram.filters import Filter
from aiogram.types import CallbackQuery

from app.bot.case_callback_scope import bind_payment_case_action, bound_case_callback
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
    case_id = int(case.id)
    action = client_action_for(case)
    buttons: list[tuple[str, str]] = []
    if action and action.callback not in {"poa_instruction", "court_status"}:
        buttons.append(
            (
                f"▶️ {action.label}",
                bind_payment_case_action(action.callback, case_id),
            )
        )
    buttons.append(("📁 Моё дело", "my_case_open"))
    if action is None or action.callback != "message_create":
        buttons.append(
            (
                "✉️ Написать команде",
                bound_case_callback("message_create", case_id),
            )
        )
    buttons.append(("🏠 Главная", "nav_home"))
    return buttons


async def _render_stale(callback: CallbackQuery, db, *, old_stage: str) -> None:
    _ctx, user, case = await _context(callback, db)
    if case is not None:
        current = get_client_visible_status(_status(case))
        action = client_action_for(case)
        next_text = action.description if action else str(case.next_action or "Откройте актуальную карточку дела.")
        case_number = str(case.case_number)
        await callback.message.edit_text(
            f"↩️ ПРЕДЫДУЩИЙ ЭТАП\n"
            f"Обращение № {case_number}\n\n"
            "СЕЙЧАС\n"
            f"Старая кнопка относится к этапу «{old_stage}». Текущее состояние: {current}. Ничего не изменено.\n\n"
            f"ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n{next_text}\n\n"
            "Продолжайте только из актуального действия ниже или из «Моё дело».",
            reply_markup=one(*_active_buttons(case)),
        )
        return

    completed = await latest_completed_case_for_user(db, user_id=user.id)
    if completed is not None:
        await callback.message.edit_text(
            f"🔒 ЭТАП ЗАВЕРШЁН\n"
            f"Обращение № {completed.case_number}\n\n"
            "СЕЙЧАС\n"
            f"Старая кнопка этапа «{old_stage}» ничего не меняет. Обращение доступно только для просмотра.\n\n"
            "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
            "Откройте итог, документы, оплаты или историю завершённого дела.",
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
        f"↩️ ПРЕДЫДУЩИЙ ЭТАП\n\n"
        "СЕЙЧАС\n"
        f"Этап «{old_stage}» больше не относится к активному обращению. Ничего не изменено.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Вернитесь на главную или начните новое обращение отдельным действием.",
        reply_markup=one(
            ("🧮 Новое обращение", "calc_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )
