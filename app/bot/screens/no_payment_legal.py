from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.payments.mode import payments_disabled
from app.domain.statuses.case_statuses import CaseStatus

router = Router()


def _status(case) -> CaseStatus:
    if isinstance(case.status, CaseStatus):
        return case.status
    return CaseStatus(str(case.status))


@router.callback_query(lambda c: payments_disabled() and c.data == "court_status")
async def court_status_without_side_effects(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text(
            "Активное дело не найдено.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return

    status = _status(case)
    if status == CaseStatus.M1_WAITING_30_DAYS:
        text = (
            "⏳ Идёт контрольный срок после фактической отправки претензии.\n\n"
            "Просмотр этого экрана не переводит дело в суд. Юрист обновит статус "
            "после истечения срока и принятия процессуального решения."
        )
        buttons = (
            ("🕘 История", "case_history_open"),
            ("💬 Задать вопрос", "message_create"),
            ("📁 Мое дело", "my_case_open"),
        )
    elif status in {
        CaseStatus.M1_COURT_STAGE,
        CaseStatus.M1_WAITING_PAYMENT_70000,
    }:
        text = (
            "🏛 Судебный этап подтверждён командой.\n\n"
            "Онлайн-оплата временно не требуется. Продолжение договорного этапа "
            "доступно отдельной кнопкой."
        )
        buttons = (
            ("Продолжить договорный этап", "pay_court_70000"),
            ("🕘 История", "case_history_open"),
            ("💬 Задать вопрос", "message_create"),
            ("📁 Мое дело", "my_case_open"),
        )
    elif status in {
        CaseStatus.M1_PAYMENT_70000_RECEIVED,
        CaseStatus.M1_ENFORCEMENT,
        CaseStatus.M1_MONEY_RECEIVED,
        CaseStatus.M1_WAITING_SUCCESS_FEE,
        CaseStatus.M1_SUCCESS_FEE_RECEIVED,
    }:
        text = "Судебный договорный этап уже пройден. Откройте текущее дело."
        buttons = (
            ("📁 Мое дело", "my_case_open"),
            ("🕘 История", "case_history_open"),
        )
    else:
        text = "Судебный этап ещё не подтверждён для текущего статуса."
        buttons = (("📁 Мое дело", "my_case_open"),)

    await callback.message.edit_text(text, reply_markup=one(*buttons))
