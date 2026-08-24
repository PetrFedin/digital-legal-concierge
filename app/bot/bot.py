import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand, BotCommandScopeDefault, CallbackQuery, Message

from app.bot.calculator_draft import CalculatorDraftNavigationMiddleware
from app.bot.client_activity import record_client_activity
from app.bot.client_message_provenance import ClientMessageProvenanceMiddleware
from app.bot.client_wording_patch import install_client_wording
from app.bot.consultation_booking_provenance import ConsultationBookingProvenanceMiddleware
from app.bot.consultation_change_provenance import ConsultationChangeProvenanceMiddleware
from app.bot.consultation_description_provenance import (
    ConsultationDescriptionProvenanceMiddleware,
)
from app.bot.consultation_route_guard import ConsultationRouteIsolationMiddleware
from app.bot.document_replacement_protection import (
    ClientDocumentUploadStageProtectionMiddleware,
    DocumentReplacementUploadProtectionMiddleware,
)
from app.bot.draft_protection import (
    DraftMessageNavigationProtectionMiddleware,
    DraftProtectionMiddleware,
)
from app.bot.lease import TelegramPollingLease
from app.bot.screens import (
    calculator,
    calculator_active_case_recovery,
    calculator_unknown_data_guard,
    common,
    consent_decision_guard,
    consent_flow,
    consent_stale_guard,
    consultation_booking_ui,
    consultation_description,
    consultation_intake,
    consultation_results,
    consultations,
    document_action_center,
    document_mutation_guard,
    document_read_scope_guard,
    document_upload_binding_guard,
    documents,
    fallback,
    history,
    m1_legal_stages,
    m1_rejection_decision_guard,
    m1_rejection_recovery,
    m1_stages,
    messages,
    my_case,
    navigation_history_guard,
    no_payment,
    no_payment_legal,
    payment_archive_guard,
    payment_received_money_guard,
    payments,
    poa_handoff,
    post_calculation,
    reply_menu_direct,
    service_contract,
    telegram_safety_composite,
)
from app.bot.security import SlidingWindowRateLimiter
from app.config import settings
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


class PollingExitedError(RuntimeError):
    pass


class DbMiddleware:
    async def __call__(self, handler, event, data):
        try:
            async with AsyncSessionLocal() as db:
                data["db"] = db
                return await handler(event, data)
        finally:
            # Activity is deliberately written in its own short transaction after
            # the handler DB session closes. It therefore cannot accidentally
            # commit unfinished legal/payment state and survives read-only
            # handler rollbacks used by presentation screens.
            await record_client_activity(event)


class FloodControlMiddleware:
    """Limit rapid Telegram actions from one user without blocking normal use."""

    def __init__(self, *, max_events: int = 8, window_seconds: int = 10):
        self.limiter = SlidingWindowRateLimiter(
            max_events=max_events,
            window_seconds=window_seconds,
        )

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if not user:
            return await handler(event, data)
        decision = self.limiter.check(user.id)
        if decision.allowed:
            return await handler(event, data)

        text = (
            "Слишком много действий подряд. "
            f"Подождите {decision.retry_after_seconds} сек. и повторите."
        )
        try:
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(text)
        except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
            logger.warning("Не удалось отправить уведомление об ограничении частоты.")
        return None


class CallbackAcknowledgeMiddleware:
    """Always close Telegram's loading spinner after callback processing."""

    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        finally:
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer()
                except TelegramBadRequest:
                    pass
                except (TelegramNetworkError, TelegramServerError):
                    logger.warning("Не удалось подтвердить callback Telegram.")


async def setup_telegram_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="status", description="Мое дело и текущий статус"),
        BotCommand(command="help", description="Помощь"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())


def build_fsm_storage() -> BaseStorage:
    backend = settings.fsm_storage_backend.strip().lower()
    if backend == "redis":
        if not settings.redis_url.strip():
            raise RuntimeError("REDIS_URL не задан для Redis FSM storage")
        return RedisStorage.from_url(settings.redis_url)
    if backend == "memory" and settings.app_env.strip().lower() != "production":
        return MemoryStorage()
    raise RuntimeError(
        "Production Telegram FSM должен использовать FSM_STORAGE_BACKEND=redis"
    )


def build_dispatcher() -> Dispatcher:
    install_client_wording()
    calculator_active_case_recovery.install_active_case_recovery_actions()
    dispatcher = Dispatcher(storage=build_fsm_storage())
    dispatcher.update.middleware(DbMiddleware())
    flood_control = FloodControlMiddleware()
    # Home/Cancel are allowed to leave the calculator, but they must not erase
    # answers already entered. The middleware snapshots only calculator FSM
    # data, lets the canonical navigation render, then restores a paused draft.
    dispatcher.message.middleware(CalculatorDraftNavigationMiddleware())
    dispatcher.callback_query.middleware(CalculatorDraftNavigationMiddleware())

    dispatcher.message.middleware(DraftMessageNavigationProtectionMiddleware())
    dispatcher.message.middleware(ConsultationDescriptionProvenanceMiddleware())
    dispatcher.message.middleware(ClientMessageProvenanceMiddleware())
    dispatcher.message.middleware(ClientDocumentUploadStageProtectionMiddleware())
    dispatcher.message.middleware(DocumentReplacementUploadProtectionMiddleware())
    dispatcher.message.middleware(flood_control)

    dispatcher.callback_query.middleware(DraftProtectionMiddleware())
    dispatcher.callback_query.middleware(ConsultationRouteIsolationMiddleware())
    dispatcher.callback_query.middleware(ConsultationBookingProvenanceMiddleware())
    dispatcher.callback_query.middleware(ConsultationChangeProvenanceMiddleware())
    dispatcher.callback_query.middleware(ConsultationDescriptionProvenanceMiddleware())
    dispatcher.callback_query.middleware(ClientMessageProvenanceMiddleware())
    dispatcher.callback_query.middleware(ClientDocumentUploadStageProtectionMiddleware())
    dispatcher.callback_query.middleware(flood_control)
    dispatcher.callback_query.middleware(CallbackAcknowledgeMiddleware())

    # Order is a business invariant. Provenance-bearing/exact-case guards must
    # see historical Telegram callbacks before legacy handlers can mutate a
    # case, payment, document or appointment. navigation_history_guard owns only
    # replay-safe/read-only screens. reply_menu_direct owns the persistent menu
    # before old trampoline handlers. payment_received_money_guard is before the
    # archive/payment routers so received money under review can never send the
    # client into another slot/payment loop. document_read_scope_guard routes
    # terminal documents to the existing read-only archive before the active-only
    # action center can turn that button into a dead end.
    for router in [
        navigation_history_guard.router,
        reply_menu_direct.router,
        common.router,
        post_calculation.router,
        calculator_active_case_recovery.router,
        calculator_unknown_data_guard.router,
        calculator.router,
        my_case.router,
        document_upload_binding_guard.router,
        document_mutation_guard.router,
        document_read_scope_guard.router,
        document_action_center.router,
        documents.router,
        no_payment_legal.router,
        consultation_results.router,
        consultation_booking_ui.router,
        consultation_description.router,
        m1_rejection_decision_guard.router,
        m1_rejection_recovery.router,
        telegram_safety_composite.router,
        consultation_intake.router,
        no_payment.router,
        payment_received_money_guard.router,
        payment_archive_guard.router,
        payments.router,
        consultations.router,
        consent_decision_guard.router,
        consent_stale_guard.router,
        consent_flow.router,
        service_contract.router,
        poa_handoff.router,
        m1_legal_stages.router,
        m1_stages.router,
        messages.router,
        history.router,
        fallback.router,
    ]:
        dispatcher.include_router(router)
    return dispatcher


async def run_bot() -> None:
    if not settings.bot_token or settings.bot_token == "CHANGE_ME":
        logger.error("BOT_TOKEN не задан. Telegram-бот не запущен.")
        return

    lease = TelegramPollingLease()
    acquired = await lease.acquire_with_wait(
        timeout_seconds=settings.telegram_singleton_wait_seconds,
        retry_seconds=settings.telegram_singleton_retry_seconds,
    )
    if not acquired:
        raise RuntimeError(
            "Telegram polling singleton lock не освобождён в установленный срок"
        )

    retry_delay = 5
    max_retry_delay = 60

    try:
        dispatcher = build_dispatcher()
        logger.info("Telegram polling singleton lock получен.")
        while True:
            bot = Bot(token=settings.bot_token)
            try:
                logger.info("Подключение к Telegram API...")
                identity = await bot.get_me()
                await bot.delete_webhook(
                    drop_pending_updates=settings.telegram_drop_pending_updates,
                    request_timeout=30,
                )
                await setup_telegram_commands(bot)
                logger.info(
                    "Telegram-бот @%s запущен в polling-режиме.",
                    identity.username,
                )
                retry_delay = 5
                await dispatcher.start_polling(
                    bot,
                    allowed_updates=dispatcher.resolve_used_update_types(),
                    polling_timeout=30,
                    handle_signals=False,
                    close_bot_session=False,
                )
                raise PollingExitedError(
                    "Telegram polling завершился без остановки процесса"
                )
            except (TelegramUnauthorizedError, PollingExitedError):
                logger.exception("Telegram polling остановлен фатальной ошибкой.")
                raise
            except TelegramRetryAfter as exc:
                wait_seconds = max(int(exc.retry_after), retry_delay)
                logger.warning(
                    "Telegram ограничил запросы. Повтор через %s сек.",
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)
            except (TelegramNetworkError, TelegramServerError, TimeoutError, OSError) as exc:
                logger.warning(
                    "Telegram временно недоступен: %s. Повтор подключения через %s сек.",
                    exc,
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
            except asyncio.CancelledError:
                logger.info("Остановка Telegram-бота.")
                raise
            except Exception:
                logger.exception(
                    "Неожиданная ошибка Telegram-бота. Повтор подключения через %s сек.",
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
            finally:
                await bot.session.close()
    finally:
        await lease.release()
        logger.info("Telegram polling singleton lock освобождён.")
