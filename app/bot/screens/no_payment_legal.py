from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.case_callback_scope import bound_case_callback
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
            "🏛 СУДЕБНЫЙ ЭТАП\n\n"
            "СЕЙЧАС\n"
            "Активное обращение не выбрано. Этот экран ничего не изменил.\n\n"
            "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
            "Откройте актуальное дело перед продолжением.",
            reply_markup=one(
                ("📁 Мои обращения", "my_cases_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    case_id = int(case.id)
    case_number = str(case.case_number)
    message_callback = bound_case_callback("message_create", case_id)
    status = _status(case)
    if status == CaseStatus.M1_WAITING_30_DAYS:
        text = (
            "⏳ КОНТРОЛЬНЫЙ СРОК\n"
            f"Обращение № {case_number}\n\n"
            "СЕЙЧАС\n"
            "Идёт контрольный срок после фактической отправки претензии. Просмотр этого экрана не переводит дело в суд.\n\n"
            "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
            "Дождитесь процессуального решения юриста; значимые изменения появятся в истории."
        )
        buttons = (
            ("🕘 История", "case_history_open"),
            ("✉️ Задать вопрос команде", message_callback),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    elif status in {
        CaseStatus.M1_COURT_STAGE,
        CaseStatus.M1_WAITING_PAYMENT_70000,
    }:
        text = (
            "🏛 СУДЕБНЫЙ ЭТАП\n"
            f"Обращение № {case_number}\n\n"
            "СЕЙЧАС\n"
            "Судебный этап подтверждён командой. Онлайн-оплата отключена: бот не создаёт платёжную ссылку и не продвигает дело по нажатию клиента.\n\n"
            "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
            "Проверьте платёжный статус. Следующий юридический этап откроется только после подтверждения фактического поступления командой."
        )
        # The previous button called pay_court_70000 even though payments are
        # disabled; it could only return another explanatory screen. Point the
        # client to the actual source of truth instead of keeping a dead action.
        buttons = (
            ("💳 Проверить оплаты", "payments_open"),
            ("🕘 История", "case_history_open"),
            ("✉️ Задать вопрос команде", message_callback),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    elif status in {
        CaseStatus.M1_PAYMENT_70000_RECEIVED,
        CaseStatus.M1_ENFORCEMENT,
        CaseStatus.M1_MONEY_RECEIVED,
        CaseStatus.M1_WAITING_SUCCESS_FEE,
        CaseStatus.M1_SUCCESS_FEE_RECEIVED,
    }:
        text = (
            "✅ СУДЕБНЫЙ ПЛАТЁЖНЫЙ ЭТАП ПРОЙДЕН\n"
            f"Обращение № {case_number}\n\n"
            "СЕЙЧАС\n"
            "Этот экран относится к уже пройденному этапу и ничего не меняет.\n\n"
            "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
            "Откройте актуальное состояние дела."
        )
        buttons = (
            ("📁 Моё дело", "my_case_open"),
            ("💳 Оплаты", "payments_open"),
            ("🕘 История", "case_history_open"),
            ("🏠 Главная", "nav_home"),
        )
    else:
        text = (
            "🏛 СУДЕБНЫЙ ЭТАП\n"
            f"Обращение № {case_number}\n\n"
            "СЕЙЧАС\n"
            "Этот шаг ещё не подтверждён для текущего статуса дела. Ничего не изменено.\n\n"
            "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
            "Вернитесь в актуальную карточку обращения."
        )
        buttons = (
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Задать вопрос команде", message_callback),
            ("🏠 Главная", "nav_home"),
        )

    await callback.message.edit_text(text, reply_markup=one(*buttons))
