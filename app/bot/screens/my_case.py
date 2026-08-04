from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone

from sqlalchemy import func, select

from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.cases.case_timeline import (
    get_case_progress_percent,
    get_client_visible_status,
)
from app.domain.payments.mode import payments_disabled
from app.models.calculation import Calculation
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.payment import Payment

router = Router()


@dataclass(frozen=True)
class ClientAction:
    label: str
    callback: str
    description: str


CLIENT_ACTIONS: dict[str, ClientAction] = {
    "CALCULATED": ClientAction(
        "Продолжить оформление",
        "consent_open",
        "Подтвердить согласие и выбрать формат юридической помощи",
    ),
    "M1_DOCUMENTS_PENDING": ClientAction(
        "Загрузить документы",
        "documents_open",
        "Загрузить ДДУ и остальные материалы по делу",
    ),
    "M1_DOCS_REQUESTED": ClientAction(
        "Добавить документы",
        "documents_open",
        "Добавить документы или исправить файлы по замечанию юриста",
    ),
    "M1_CONTRACT_READY": ClientAction(
        "Открыть договор",
        "contract_open",
        "Ознакомиться с договором и подтвердить продолжение работы",
    ),
    "M1_WAITING_PAYMENT_30000": ClientAction(
        "Продолжить оформление",
        "pay_start_30000",
        "Продолжить к этапу доверенности без онлайн-оплаты",
    ),
    "M1_POWER_OF_ATTORNEY": ClientAction(
        "Оформить доверенность",
        "poa_instruction",
        "Открыть инструкцию по оформлению доверенности",
    ),
    "M1_WAITING_PAYMENT_70000": ClientAction(
        "Продолжить исполнение",
        "pay_court_70000",
        "Продолжить к исполнению решения без онлайн-оплаты",
    ),
    "M1_MONEY_RECEIVED": ClientAction(
        "Завершить финансовый этап",
        "pay_success_fee",
        "Подтвердить финальный этап сопровождения",
    ),
    "M1_WAITING_SUCCESS_FEE": ClientAction(
        "Завершить финансовый этап",
        "pay_success_fee",
        "Завершить финансовый этап без онлайн-оплаты",
    ),
    "M2_DESCRIPTION_PENDING": ClientAction(
        "Описать вопрос",
        "consult_description_start",
        "Кратко описать ситуацию для подготовки юриста",
    ),
    "M2_DOCUMENTS_OPTIONAL": ClientAction(
        "Добавить документы",
        "documents_open",
        "Добавить материалы к консультации или продолжить без них",
    ),
    "M2_SLOT_PENDING": ClientAction(
        "Выбрать время",
        "consult_slot_open",
        "Выбрать доступную дату и время консультации",
    ),
    "M2_PAYMENT_PENDING": ClientAction(
        "Подтвердить запись",
        "consult_pay",
        "Подтвердить консультацию без онлайн-оплаты",
    ),
    "M2_CONSULTATION_BOOKED": ClientAction(
        "Открыть запись",
        "consultation_booked_open",
        "Проверить дату, время и данные консультации",
    ),
}


def money(value):
    return "—" if value is None else f"{value:,.2f}".replace(",", " ") + " ₽"


def route_label(route: str | None) -> str:
    return {
        "M1": "Ведение дела",
        "M2": "Консультация",
    }.get(str(route or ""), "Юридическое обращение")


def client_action_for(case) -> ClientAction | None:
    return CLIENT_ACTIONS.get(str(case.status))


def next_action_text(case) -> str:
    action = client_action_for(case)
    if action:
        return action.description
    return case.next_action or "Ожидайте обновления от юридической команды"


def format_consultation_time(consultation: Consultation | None) -> str | None:
    if not consultation or not consultation.scheduled_at:
        return None
    value = consultation.scheduled_at
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.strftime("%d.%m.%Y в %H:%M UTC")


async def _active_case_context(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    return ctx, user, case


async def _render_case(callback: CallbackQuery, db, *, notice: str | None = None):
    _, _, case = await _active_case_context(callback, db)
    if not case:
        text = "📁 У вас пока нет активного дела.\n\nВыберите, с чего начать:"
        if notice:
            text = f"{notice}\n\n{text}"
        await callback.message.edit_text(
            text,
            reply_markup=one(
                ("🧮 Рассчитать неустойку", "calc_start"),
                ("💬 Записаться на консультацию", "calc_to_m2"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    calc = (
        await db.execute(
            select(Calculation).where(Calculation.case_id == case.id)
        )
    ).scalars().first()
    documents_count = (
        await db.execute(
            select(func.count(Document.id)).where(Document.case_id == case.id)
        )
    ).scalar_one()
    consultation = (
        await db.execute(
            select(Consultation)
            .where(Consultation.case_id == case.id)
            .order_by(Consultation.created_at.desc())
            .limit(1)
        )
    ).scalars().first()

    payments_count = 0
    if not payments_disabled():
        payments_count = (
            await db.execute(
                select(func.count(Payment.id))
                .where(Payment.case_id == case.id)
                .where(Payment.status.in_(["PENDING", "WAITING_CONFIRMATION"]))
            )
        ).scalar_one()

    lines = []
    if notice:
        lines.extend([notice, ""])
    lines.extend(
        [
            "📁 Моё дело",
            "",
            f"Номер: {case.case_number}",
            f"Услуга: {route_label(case.route)}",
            f"Статус: {get_client_visible_status(case.status)}",
            f"Прогресс: {get_case_progress_percent(case.status)}%",
            "",
            "Что дальше:",
            next_action_text(case),
        ]
    )

    if calc and str(case.route) == "M1":
        lines.extend(
            [
                "",
                f"📊 Расчёт неустойки: {money(calc.penalty_amount)}",
                f"Просрочка: {calc.delay_days} дн.",
            ]
        )

    consultation_time = format_consultation_time(consultation)
    if consultation_time:
        lines.extend(["", f"🗓 Консультация: {consultation_time}"])

    lines.extend(["", f"📄 Документы: {documents_count}"])
    if not payments_disabled() and payments_count:
        lines.append(f"💳 Ожидают оплаты: {payments_count}")

    action = client_action_for(case)
    buttons: list[tuple[str, str]] = []
    if action:
        buttons.append(
            (
                f"▶️ {action.label}",
                f"next_action:{case.id}:{case.status}",
            )
        )
    else:
        buttons.append(("🔄 Обновить статус", "my_case_open"))

    buttons.append(("📄 Документы", "documents_open"))
    if not payments_disabled():
        buttons.append(("💳 Оплаты", "payments_open"))
    buttons.extend(
        [
            ("🕘 История", "case_history_open"),
            ("💬 Связаться с юристом", "contact_lawyer"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    await callback.message.edit_text("\n".join(lines), reply_markup=one(*buttons))


@router.callback_query(lambda c: c.data == "my_case_open")
async def my_case(callback: CallbackQuery, db):
    await _render_case(callback, db)


@router.callback_query(lambda c: c.data.startswith("next_action:"))
async def next_action(callback: CallbackQuery, db):
    parts = str(callback.data or "").split(":")
    requested_case_id: int | None = None
    requested_status: str | None = None

    if len(parts) >= 3:
        try:
            requested_case_id = int(parts[1])
        except ValueError:
            requested_case_id = None
        requested_status = parts[2]
    elif len(parts) == 2:
        # Совместимость со старыми сообщениями, где callback содержал только статус.
        requested_status = parts[1]

    _, _, case = await _active_case_context(callback, db)
    if not case:
        await _render_case(
            callback,
            db,
            notice="Это дело уже завершено или больше не активно.",
        )
        return

    current_status = str(case.status)
    if requested_case_id is not None and requested_case_id != case.id:
        await _render_case(
            callback,
            db,
            notice="Вы открыли кнопку от другого дела. Показан актуальный статус.",
        )
        return
    if requested_status and requested_status != current_status:
        await _render_case(
            callback,
            db,
            notice="Статус дела уже изменился. Показан актуальный следующий шаг.",
        )
        return

    action = client_action_for(case)
    if not action:
        await _render_case(
            callback,
            db,
            notice="Сейчас действие от вас не требуется.",
        )
        return

    await callback.message.edit_text(
        f"▶️ {action.label}\n\n{action.description}",
        reply_markup=one(
            (action.label, action.callback),
            ("↩️ Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )
