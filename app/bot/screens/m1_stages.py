import logging
from datetime import datetime, timezone

from aiogram import Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramServerError,
)
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.cases.case_history import add_case_history_event
from app.domain.messages.message_service import MessageService
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.mode import payments_disabled
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.domain.retention.content_visibility import private_case_content_available_clause
from app.models.case import Case
from app.models.payment import Payment

router = Router()
logger = logging.getLogger(__name__)


def _status(case) -> CaseStatus:
    if isinstance(case.status, CaseStatus):
        return case.status
    return CaseStatus(str(case.status))


async def _case(callback: CallbackQuery, db, *, lock: bool = False):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if getattr(user, "is_blocked", False):
        return ctx, user, None
    if case and lock:
        # Refresh under the row lock: old buttons cannot act on an old status
        # or on content whose erasure was approved in a different transaction.
        case = (await db.execute(
            select(Case).where(
                Case.id == case.id,
                Case.client_id == user.id,
                private_case_content_available_clause(),
            ).with_for_update().execution_options(populate_existing=True)
        )).scalar_one_or_none()
    return ctx, user, case


def _targets_case(callback: CallbackQuery, action: str, case) -> bool:
    return str(callback.data or "") == f"{action}:{case.id}"


async def _initial_payment_confirmed(db, case_id: int) -> bool:
    return (await db.scalar(select(Payment.id).where(
        Payment.case_id == case_id,
        Payment.payment_code == PaymentCode.M1_INITIAL_PAYMENT,
        Payment.status == PaymentStatus.PAID,
    ).limit(1))) is not None


async def _present_committed_result(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup,
) -> None:
    """Show a durable M1 result without converting Telegram UI failure into write failure."""

    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
        return
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            return
        logger.warning("Не удалось обновить M1-экран после сохранения: %s", error)
    except (TelegramNetworkError, TelegramServerError) as error:
        logger.warning("Telegram недоступен после сохранения M1-действия: %s", error)

    try:
        await callback.message.answer(text, reply_markup=reply_markup)
    except (TelegramBadRequest, TelegramNetworkError, TelegramServerError) as error:
        # The database transaction is already committed. Do not invite the user
        # to repeat a legal/process mutation merely because Telegram is stale.
        logger.warning("Не удалось показать сохранённый M1-результат: %s", error)


async def _show_stale_stage(
    callback: CallbackQuery,
    text: str,
    *,
    include_documents: bool = False,
) -> None:
    buttons: list[tuple[str, str]] = []
    if include_documents:
        buttons.append(("📄 Документы", "documents_open"))
    buttons.extend(
        [
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    await _present_committed_result(
        callback,
        text,
        reply_markup=one(*buttons),
    )


@router.callback_query(lambda c: c.data == "contract_open")
async def contract_open(callback: CallbackQuery, db):
    _ctx, _user, case = await _case(callback, db)
    if not case or _status(case) not in {
        CaseStatus.M1_CONTRACT_READY, CaseStatus.M1_WAITING_PAYMENT_30000,
    }:
        await _show_stale_stage(callback, "Договор недоступен на текущем этапе. Откройте актуальное дело.")
        return
    await callback.message.edit_text(
        f"📝 Договор по делу № {case.case_number}\n\n"
        "Проверьте реквизиты, объём услуг, стоимость и порядок взаимодействия "
        "в договоре, который направила команда.\n\n"
        "Кнопка ниже фиксирует ваше подтверждение в системе, но не заменяет "
        "обязательную форму подписи, если она предусмотрена договором.",
        reply_markup=one(
            ("Подтвердить договор", f"contract_sign:{case.id}"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("📄 Документы", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(
    lambda c: c.data == "contract_sign" or str(c.data or "").startswith("contract_sign:")
)
async def contract_sign(callback: CallbackQuery, db):
    ctx, user, case = await _case(callback, db, lock=True)
    if not case:
        await _show_stale_stage(
            callback,
            "Активное дело не найдено. Откройте актуальную карточку перед новым действием.",
        )
        return

    if not _targets_case(callback, "contract_sign", case):
        await _show_stale_stage(
            callback,
            "Кнопка договора устарела или относится к другому обращению. "
            "Дело не изменено. Откройте договор из актуальной карточки.",
        )
        return

    status = _status(case)
    if status not in {
        CaseStatus.M1_CONTRACT_READY,
        CaseStatus.M1_WAITING_PAYMENT_30000,
    }:
        await _show_stale_stage(
            callback,
            "Эта кнопка договора больше не соответствует текущему этапу. "
            "Дело не изменено — откройте актуальный следующий шаг.",
            include_documents=True,
        )
        return

    if status == CaseStatus.M1_WAITING_PAYMENT_30000 and not case.contract_signed_at:
        await _show_stale_stage(
            callback, "Подписание договора ещё не зафиксировано командой. Уточните следующий шаг у юриста."
        )
        return

    service = PaymentService(db)
    try:
        if status == CaseStatus.M1_CONTRACT_READY:
            if case.contract_signed_at is None:
                case.contract_signed_at = datetime.now(timezone.utc)
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M1_WAITING_PAYMENT_30000,
                actor_type="client",
                actor_id=user.id,
                comment="Клиент подтвердил договор",
            )
        payment = await service.get_or_create_payment(
            case=case,
            payment_code=PaymentCode.M1_INITIAL_PAYMENT,
        )
        await db.commit()
    except (RuntimeError, ValueError) as error:
        await db.rollback()
        await _show_stale_stage(
            callback,
            f"Подтверждение договора пока не сохранено: {error}\n\n"
            "Проверьте актуальный этап и повторите действие только после обновления карточки.",
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось подтвердить договор M1")
        await _show_stale_stage(
            callback,
            "Подтверждение договора временно не сохранено. Данные дела не изменены.",
        )
        return

    from app.bot.screens.payments import money

    if payments_disabled():
        text = (
            "✅ Подтверждение договора сохранено.\n\n"
            f"Первый платёж: {money(payment.amount)}. Онлайн-оплата сейчас отключена, "
            "поэтому этап подтвердит команда после фактической фиксации платежа. "
            "До этого доверенность не откроется автоматически."
        )
        markup = one(
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        )
    else:
        text = (
            "✅ Подтверждение договора сохранено.\n\n"
            f"Следующий шаг — первый платёж {money(payment.amount)}."
        )
        markup = one(
            (f"💳 Оплатить {money(payment.amount)}", "pay_start_30000"),
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        )
    await _present_committed_result(callback, text, reply_markup=markup)


@router.callback_query(lambda c: c.data == "poa_instruction")
async def poa_instruction(callback: CallbackQuery, db):
    _ctx, _user, case = await _case(callback, db, lock=True)
    if not case or _status(case) not in {
        CaseStatus.M1_PAYMENT_30000_RECEIVED,
        CaseStatus.M1_POWER_OF_ATTORNEY,
    }:
        await _show_stale_stage(
            callback, "Инструкция по доверенности недоступна на текущем этапе.",
            include_documents=True,
        )
        return
    if not await _initial_payment_confirmed(db, case.id):
        await _show_stale_stage(
            callback, "Инструкция откроется после подтверждения первого платежа командой.",
        )
        return

    try:
        if case.poa_instruction_sent_at is None:
            case.poa_instruction_sent_at = datetime.now(timezone.utc)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Не удалось сохранить открытие инструкции по доверенности")
        await _show_stale_stage(
            callback, "Инструкция временно недоступна. Повторите открытие из карточки дела."
        )
        return

    reported = case.poa_client_confirmed_at is not None
    text = (
        f"📑 Доверенность по делу № {case.case_number}\n\n"
        "Оформите доверенность и нотариальные копии по инструкции юриста. "
        "После передачи документов сообщите команде о готовности. "
        "Получение и правильность доверенности подтвердит юрист."
    )
    if reported:
        text += (
            "\n\nВаше сообщение сохранено. Ожидаем проверки доверенности командой. "
            "Повторное подтверждение не требуется; при необходимости можно добавить документы."
        )
    buttons = []
    if not reported and _status(case) == CaseStatus.M1_POWER_OF_ATTORNEY:
        buttons.append(("Сообщить о передаче доверенности", f"poa_done:{case.id}"))
    buttons.extend([
        ("📄 Добавить документ", "documents_open"),
        ("✉️ Задать вопрос команде", "message_create"),
        ("📁 Моё дело", "my_case_open"),
        ("🏠 Главная", "nav_home"),
    ])
    await _present_committed_result(callback, text, reply_markup=one(*buttons))


@router.callback_query(
    lambda c: c.data in {"poa_done", "poa_sent_confirm"}
    or str(c.data or "").startswith(("poa_done:", "poa_sent_confirm:"))
)
async def poa_done(callback: CallbackQuery, db):
    _ctx, user, case = await _case(callback, db, lock=True)
    if not case:
        await _show_stale_stage(
            callback,
            "Активное дело не найдено. Подтверждение доверенности не выполнялось.",
            include_documents=True,
        )
        return
    if not any(
        _targets_case(callback, action, case) for action in ("poa_done", "poa_sent_confirm")
    ):
        await _show_stale_stage(
            callback,
            "Кнопка доверенности устарела или относится к другому обращению. "
            "Дело не изменено. Откройте инструкцию из актуальной карточки.",
            include_documents=True,
        )
        return

    status = _status(case)
    if status == CaseStatus.M1_POWER_OF_ATTORNEY:
        if not await _initial_payment_confirmed(db, case.id):
            await _show_stale_stage(
                callback,
                "Сначала требуется подтверждение первого платежа. Дело не изменено.",
            )
            return
        if case.poa_client_confirmed_at is not None:
            await _show_stale_stage(
                callback,
                "Ваше сообщение о передаче доверенности уже сохранено. "
                "Повторная запись не создавалась — ожидаем проверки команды.",
                include_documents=True,
            )
            return
        try:
            case.poa_client_confirmed_at = datetime.now(timezone.utc)
            report = await MessageService(db).create_client_message(
                case=case, user_id=user.id,
                text="Сообщаю о передаче оформленной доверенности. Прошу команду проверить получение и документы.",
            )
            await add_case_history_event(
                db, actor_type="client", actor_id=user.id, case_id=case.id,
                action="POA_READY_REPORTED",
                new_value={
                    "poa_client_confirmed_at": case.poa_client_confirmed_at.isoformat(),
                    "message_id": report.id,
                },
            )
            await NotificationEngine(db).emit(
                event_code="CLIENT_MESSAGE_RECEIVED", case_id=case.id, user_id=user.id,
                payload={
                    "case_number": case.case_number,
                    "category": "Доверенность",
                    "urgency": "Обычная",
                    "assignment": (
                        "Назначенный юрист и администратор"
                        if case.assigned_lawyer_id else "Требуется распределение"
                    ),
                    "text": "Клиент сообщил о передаче доверенности. Требуется проверка команды.",
                },
                dedupe_key=f"case:{case.id}:poa-ready-reported",
            )
            await db.commit()
        except (RuntimeError, ValueError) as error:
            await db.rollback()
            await _show_stale_stage(
                callback, f"Сообщение о доверенности пока не сохранено: {error}",
                include_documents=True,
            )
            return
        except Exception:
            await db.rollback()
            logger.exception("Не удалось сохранить сообщение о доверенности")
            await _show_stale_stage(
                callback,
                "Сообщение о доверенности временно не сохранено. Данные дела не изменены.",
                include_documents=True,
            )
            return

        await _present_committed_result(
            callback,
            "✅ Информация о передаче доверенности сохранена для команды.\n\n"
            "После проверки юрист подтвердит получение, и статус обновится в разделе «Моё дело». "
            "Отправка претензии и начало 30-дневного срока фиксируются отдельным действием.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("📄 Документы", "documents_open"),
                ("📑 Инструкция по доверенности", "poa_instruction"),
                ("✉️ Задать вопрос команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if status in {
        CaseStatus.M1_POA_RECEIVED, CaseStatus.M1_CLAIM_PREPARATION,
        CaseStatus.M1_CLAIM_SENT, CaseStatus.M1_WAITING_30_DAYS,
        CaseStatus.M1_LAWSUIT_PREPARATION, CaseStatus.M1_LAWSUIT_FILED,
        CaseStatus.M1_COURT_STAGE, CaseStatus.M1_DECISION_RECEIVED,
        CaseStatus.M1_WAITING_PAYMENT_70000, CaseStatus.M1_PAYMENT_70000_RECEIVED,
        CaseStatus.M1_ENFORCEMENT, CaseStatus.M1_MONEY_RECEIVED,
        CaseStatus.M1_WAITING_SUCCESS_FEE, CaseStatus.M1_SUCCESS_FEE_RECEIVED,
        CaseStatus.M1_CLOSED,
    }:
        await _show_stale_stage(
            callback,
            "Доверенность уже подтверждена командой. Повторная запись не создавалась — "
            "откройте актуальный этап дела.",
            include_documents=True,
        )
        return
    await _show_stale_stage(
        callback,
        "Сообщение о доверенности недоступно на текущем этапе. Дело не изменено.",
        include_documents=True,
    )



@router.callback_query(lambda c: c.data == "court_status")
async def court_status(callback: CallbackQuery, db):
    _ctx, _user, case = await _case(callback, db)
    if not case:
        await _show_stale_stage(
            callback,
            "Активное дело не найдено. Откройте актуальную карточку.",
        )
        return

    status = _status(case)
    if status == CaseStatus.M1_WAITING_30_DAYS:
        deadline = (
            case.claim_waiting_until.astimezone(timezone.utc).strftime("%d.%m.%Y")
            if case.claim_waiting_until
            else None
        )
        deadline_text = (
            f" Контрольная дата: {deadline}."
            if deadline
            else " Контрольная дата уточняется юридической командой."
        )
        text = (
            "⏳ Идёт контрольный срок после отправки претензии.\n\n"
            + deadline_text
            + "\n\nПросмотр этого экрана ничего не меняет. После истечения 30 дней "
            "юрист сможет начать подготовку иска."
        )
        buttons = (
            ("🕘 История", "case_history_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    elif status == CaseStatus.M1_LAWSUIT_PREPARATION:
        text = (
            "⚖️ Юрист готовит иск.\n\n"
            "От вас сейчас не требуется отдельного действия. После фактической "
            "подачи иска этап будет обновлён в истории дела."
        )
        buttons = (
            ("🕘 История", "case_history_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    elif status == CaseStatus.M1_LAWSUIT_FILED:
        text = (
            "⚖️ Иск подан.\n\n"
            "Ожидаем начала судебного этапа. Значимые события фиксируются "
            "юристом и появляются в истории дела."
        )
        buttons = (
            ("🕘 История", "case_history_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    elif status == CaseStatus.M1_COURT_STAGE:
        text = (
            "🏛 Судебный этап открыт юристом.\n\n"
            "Значимые события будут появляться в истории дела. Второй платёж "
            "станет доступен только после получения и фиксации решения суда."
        )
        buttons = (
            ("🕘 История", "case_history_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    elif status == CaseStatus.M1_DECISION_RECEIVED:
        text = (
            "✅ Решение суда получено и зафиксировано.\n\n"
            "Система готовит следующий договорный платёж. До перехода дела в "
            "платёжный этап кнопка оплаты не показывается."
        )
        buttons = (
            ("🕘 История", "case_history_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    elif status == CaseStatus.M1_WAITING_PAYMENT_70000:
        if payments_disabled():
            text = (
                "💳 Второй платёж открыт после судебного решения.\n\n"
                "Онлайн-оплата сейчас отключена. Команда зафиксирует фактический "
                "платёж и только после этого откроет исполнительный этап."
            )
            buttons = (
                ("✉️ Задать вопрос команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            )
        else:
            text = (
                "💳 Второй платёж открыт после судебного решения.\n\n"
                "После подтверждения оплаты система откроет исполнительный этап."
            )
            buttons = (
                ("💳 Перейти к оплате", "pay_court_70000"),
                ("🕘 История", "case_history_open"),
                ("✉️ Задать вопрос команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            )
    elif status in {
        CaseStatus.M1_PAYMENT_70000_RECEIVED,
        CaseStatus.M1_ENFORCEMENT,
        CaseStatus.M1_MONEY_RECEIVED,
        CaseStatus.M1_WAITING_SUCCESS_FEE,
        CaseStatus.M1_SUCCESS_FEE_RECEIVED,
        CaseStatus.M1_CLOSED,
    }:
        text = "Судебный платёжный этап уже пройден. Откройте текущее состояние дела."
        buttons = (
            ("📁 Моё дело", "my_case_open"),
            ("🕘 История", "case_history_open"),
            ("🏠 Главная", "nav_home"),
        )
    else:
        text = "Судебный этап ещё не открыт для текущего состояния дела."
        buttons = (
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        )
    await callback.message.edit_text(text, reply_markup=one(*buttons))


@router.callback_query(lambda c: c.data == "pay_court_70000")
async def pay_court(callback: CallbackQuery, db):
    _ctx, _user, case = await _case(callback, db)
    if not case:
        await _show_stale_stage(
            callback,
            "Активное дело не найдено. Платёж не создавался.",
        )
        return
    if _status(case) != CaseStatus.M1_WAITING_PAYMENT_70000:
        await _show_stale_stage(
            callback,
            "Эта кнопка оплаты больше не соответствует текущему этапу. "
            "Новый платёж не создавался.",
        )
        return
    if payments_disabled():
        await _show_stale_stage(
            callback,
            "Онлайн-оплата сейчас отключена. Второй платёж фиксирует команда; "
            "исполнительный этап откроется только после подтверждения.",
        )
        return

    from app.bot.screens.payments import start_payment

    await start_payment(callback, db, PaymentCode.M1_COURT_PAYMENT)


@router.callback_query(lambda c: c.data == "pay_success_fee")
async def pay_success_fee(callback: CallbackQuery, db):
    ctx, _user, case = await _case(callback, db)
    if not case:
        await _show_stale_stage(
            callback,
            "Активное дело не найдено. Финальный платёж не создавался.",
        )
        return

    status = _status(case)
    if status == CaseStatus.M1_ENFORCEMENT:
        await _show_stale_stage(
            callback,
            "Финальный платёж ещё не рассчитан: сначала команда должна "
            "зафиксировать фактическое получение денег.",
        )
        return
    if status not in {
        CaseStatus.M1_MONEY_RECEIVED,
        CaseStatus.M1_WAITING_SUCCESS_FEE,
    }:
        await _show_stale_stage(
            callback,
            "Эта кнопка финального платежа больше не соответствует текущему этапу. "
            "Новый платёж не создавался.",
        )
        return

    existing = (
        await db.execute(
            select(Payment)
            .where(Payment.case_id == case.id)
            .where(Payment.payment_code == PaymentCode.M1_SUCCESS_FEE)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
        )
    ).scalars().first()
    service = PaymentService(db)
    try:
        amount = await service.estimate_success_fee_for_case(case.id)
        if existing and existing.amount != amount:
            raise ValueError(
                "Сумма существующего финального платежа не соответствует "
                "фактическому поступлению. Требуется ручная проверка."
            )
        if status == CaseStatus.M1_MONEY_RECEIVED:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M1_WAITING_SUCCESS_FEE,
                actor_type="system",
                actor_id=None,
                comment="Открыт финальный договорный платёж после получения денег",
            )
        if existing:
            payment = existing
            case.success_fee_amount = amount
        else:
            payment = await service.get_or_create_payment(
                case=case,
                payment_code=PaymentCode.M1_SUCCESS_FEE,
                amount=amount,
            )
        if not payments_disabled():
            payment = await service.create_payment_link(payment)
        await db.commit()
    except (RuntimeError, ValueError) as error:
        await db.rollback()
        await _show_stale_stage(
            callback,
            f"Финальный платёж пока не открыт: {error}\n\n"
            "Данные дела сохранены. Проверьте актуальный этап перед повтором.",
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось открыть финальный платёж M1")
        await _show_stale_stage(
            callback,
            "Финальный платёж временно не открыт. Данные дела не изменены.",
        )
        return

    from app.bot.screens.payments import money, payment_keyboard

    if payments_disabled():
        text = (
            "💳 Финальный платёж открыт.\n\n"
            f"Сумма: {money(payment.amount)}\n\n"
            "Онлайн-оплата сейчас отключена. Команда подтвердит фактический платёж; "
            "закрытие дела выполняется отдельно после подтверждения платежа и завершения работы."
        )
        markup = one(
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        )
    else:
        text = (
            "💳 Финальный платёж\n\n"
            f"Сумма: {money(payment.amount)}\n\n"
            "После подтверждения оплаты команда подготовит финальные документы и подтвердит завершение работы."
        )
        markup = payment_keyboard(payment)
    await _present_committed_result(callback, text, reply_markup=markup)
