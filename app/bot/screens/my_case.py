from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy import func, select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.cases.case_timeline import get_case_progress_percent, get_client_visible_status
from app.models.calculation import Calculation
from app.models.document import Document
from app.models.payment import Payment

router = Router()


def money(value):
    return "—" if value is None else f"{value:,.2f}".replace(",", " ") + " ₽"


def route_label(route: str | None) -> str:
    return {
        "M1": "Судебное сопровождение",
        "M2": "Консультация",
    }.get(route, "Новое обращение")


def case_button_label(case, selected_case_id: int | None) -> str:
    marker = "✅" if case.id == selected_case_id else "📁"
    archived = " · архив" if case.is_archived else ""
    return f"{marker} {case.case_number} · {route_label(case.route)}{archived}"


@router.callback_query(lambda c: c.data in {"my_case_open", "cases_list_open"})
async def cases_list(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    cases = await ctx.case_service.list_cases_for_user(user.id, include_archived=False)

    if not cases:
        await callback.message.edit_text(
            "📁 У вас пока нет обращений.\n\nСоздайте первое обращение: расчет неустойки или консультация с юристом.",
            reply_markup=one(
                ("🧮 Рассчитать неустойку", "calc_start"),
                ("💬 Новая консультация", "new_case_consultation"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    selected = await ctx.case_service.get_selected_case_for_user(user)
    rows = [
        (case_button_label(case, selected.id if selected else None), f"case_open:{case.id}")
        for case in cases
    ]
    rows += [
        ("➕ Новое обращение", "new_case_open"),
        ("🗄 Архив", "cases_archive_open"),
        ("🏠 Главная", "nav_home"),
    ]
    await db.commit()
    await callback.message.edit_text(
        "📁 Мои дела\n\nВыберите обращение. Отмеченное галочкой используется для документов, сообщений, оплат и консультаций.",
        reply_markup=one(*rows),
    )


@router.callback_query(lambda c: c.data.startswith("case_open:"))
async def case_open(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case_id = int(callback.data.split(":", 1)[1])
    try:
        case = await ctx.case_service.select_case(user=user, case_id=case_id)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await db.commit()
    await render_case_card(callback, db, case)


async def render_case_card(callback: CallbackQuery, db, case):
    calc = (await db.execute(select(Calculation).where(Calculation.case_id == case.id))).scalars().first()
    documents_count = (
        await db.execute(select(func.count(Document.id)).where(Document.case_id == case.id))
    ).scalar_one()
    payments_count = (
        await db.execute(
            select(func.count(Payment.id))
            .where(Payment.case_id == case.id)
            .where(Payment.status.in_(["PENDING", "WAITING_CONFIRMATION"]))
        )
    ).scalar_one()

    text = (
        "📁 Карточка дела\n\n"
        f"Номер: {case.case_number}\n"
        f"Название: {case.title or route_label(case.route)}\n"
        f"Маршрут: {route_label(case.route)}\n"
        f"Статус: {get_client_visible_status(case.status)}\n"
        f"Прогресс: {get_case_progress_percent(case.status)}%\n\n"
        f"Следующий шаг: {case.next_action or 'ожидать обновления'}\n\n"
        f"📊 Расчет: {money(calc.penalty_amount if calc else None)}\n"
        f"Дней просрочки: {calc.delay_days if calc else '—'}\n"
        f"📄 Документы: {documents_count}\n"
        f"💳 Ожидают оплаты: {payments_count}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=one(
            ("Следующий шаг", f"next_action:{case.status}"),
            ("📄 Документы", "documents_open"),
            ("💳 Оплаты", "payments_open"),
            ("🕘 История", "case_history_open"),
            ("💬 Связаться с юристом", "contact_lawyer"),
            ("🗄 В архив", f"case_archive_confirm:{case.id}"),
            ("⬅️ Все дела", "cases_list_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "new_case_open")
async def new_case_open(callback: CallbackQuery):
    await callback.message.edit_text(
        "➕ Новое обращение\n\nВыберите тип. Существующие дела останутся доступными.",
        reply_markup=one(
            ("🧮 Неустойка по ДДУ", "new_case_calculation"),
            ("💬 Консультация", "new_case_consultation"),
            ("⬅️ Мои дела", "cases_list_open"),
        ),
    )


@router.callback_query(lambda c: c.data == "new_case_calculation")
async def new_case_calculation(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    await ctx.case_service.create_case(client=user, title="Расчет неустойки по ДДУ")
    await db.commit()
    await callback.message.edit_text(
        "Новое дело создано. Теперь заполните данные для расчета.",
        reply_markup=one(("Начать расчет", "calc_start"), ("📁 Мои дела", "cases_list_open")),
    )


@router.callback_query(lambda c: c.data == "new_case_consultation")
async def new_case_consultation(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.create_case(
        client=user,
        route="M2",
        status="M2_DESCRIPTION_PENDING",
        title="Консультация с юристом",
    )
    await db.commit()
    await callback.message.edit_text(
        f"Создано обращение {case.case_number}.\n\nОпишите ситуацию и вопрос для юриста.",
        reply_markup=one(("Описать вопрос", "consult_description_start"), ("📁 Мои дела", "cases_list_open")),
    )


@router.callback_query(lambda c: c.data.startswith("case_archive_confirm:"))
async def case_archive_confirm(callback: CallbackQuery):
    case_id = int(callback.data.split(":", 1)[1])
    await callback.message.edit_text(
        "Переместить дело в архив? Данные, документы и история сохранятся.",
        reply_markup=one(
            ("Да, архивировать", f"case_archive:{case_id}"),
            ("Отмена", f"case_open:{case_id}"),
        ),
    )


@router.callback_query(lambda c: c.data.startswith("case_archive:"))
async def case_archive(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case_id = int(callback.data.split(":", 1)[1])
    case = await ctx.case_service.get_case_for_user(user_id=user.id, case_id=case_id)
    if not case:
        await callback.answer("Дело не найдено", show_alert=True)
        return
    await ctx.case_service.archive_case(user=user, case=case)
    await db.commit()
    await callback.message.edit_text(
        "Дело перемещено в архив.",
        reply_markup=one(("📁 Мои дела", "cases_list_open"), ("🗄 Архив", "cases_archive_open")),
    )


@router.callback_query(lambda c: c.data == "cases_archive_open")
async def cases_archive_open(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    all_cases = await ctx.case_service.list_cases_for_user(user.id, include_archived=True)
    archived = [case for case in all_cases if case.is_archived]
    if not archived:
        await callback.message.edit_text(
            "🗄 Архив пуст.",
            reply_markup=one(("⬅️ Мои дела", "cases_list_open"), ("🏠 Главная", "nav_home")),
        )
        return
    rows = [
        (f"↩️ {case.case_number} · {route_label(case.route)}", f"case_restore:{case.id}")
        for case in archived
    ]
    rows.append(("⬅️ Мои дела", "cases_list_open"))
    await callback.message.edit_text(
        "🗄 Архив дел\n\nНажмите на дело, чтобы восстановить его и сделать выбранным.",
        reply_markup=one(*rows),
    )


@router.callback_query(lambda c: c.data.startswith("case_restore:"))
async def case_restore(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case_id = int(callback.data.split(":", 1)[1])
    case = await ctx.case_service.get_case_for_user(user_id=user.id, case_id=case_id)
    if not case:
        await callback.answer("Дело не найдено", show_alert=True)
        return
    await ctx.case_service.restore_case(user=user, case=case)
    await db.commit()
    await callback.message.edit_text(
        f"Дело {case.case_number} восстановлено и выбрано.",
        reply_markup=one(("Открыть дело", f"case_open:{case.id}"), ("📁 Мои дела", "cases_list_open")),
    )


@router.callback_query(lambda c: c.data.startswith("next_action:"))
async def next_action(callback: CallbackQuery):
    status = callback.data.split(":", 1)[1]
    mapping = {
        "CALCULATED": "consent_open",
        "M1_DOCUMENTS_PENDING": "documents_open",
        "M1_DOCS_REQUESTED": "documents_open",
        "M1_CONTRACT_READY": "contract_open",
        "M1_WAITING_PAYMENT_30000": "pay_start_30000",
        "M1_POWER_OF_ATTORNEY": "poa_instruction",
        "M1_WAITING_30_DAYS": "court_status",
        "M1_COURT_STAGE": "court_status",
        "M1_WAITING_PAYMENT_70000": "pay_court_70000",
        "M1_ENFORCEMENT": "pay_success_fee",
        "M1_WAITING_SUCCESS_FEE": "pay_success_fee",
        "M2_DESCRIPTION_PENDING": "consult_description_start",
        "M2_DOCUMENTS_OPTIONAL": "documents_open",
        "M2_SLOT_PENDING": "consult_slot_open",
        "M2_PAYMENT_PENDING": "consult_pay",
        "M2_CONSULTATION_BOOKED": "consultation_booked_open",
    }
    target = mapping.get(status)
    if not target:
        await callback.message.edit_text(
            "Сейчас действие не требуется. Мы сообщим, когда появится следующий шаг.",
            reply_markup=one(("📁 Мои дела", "cases_list_open"), ("🏠 Главная", "nav_home")),
        )
        return
    await callback.message.edit_text(
        "Откройте следующий шаг:",
        reply_markup=one(("Перейти", target), ("📁 Мои дела", "cases_list_open")),
    )
