from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import DocumentUploadStates
from app.domain.statuses.case_statuses import CaseStatus
from app.models.document import Document

logger = logging.getLogger(__name__)

_REPLACEMENT_STATUSES = {"REJECTED", "NEEDS_REUPLOAD"}
_ARCHIVED_STATUS = "ARCHIVED"
_M1_CLIENT_UPLOAD_STATUSES = {
    CaseStatus.M1_DOCUMENTS_PENDING,
    CaseStatus.M1_DOCUMENTS_RECEIVED,
    CaseStatus.M1_DOCS_REQUESTED,
}
_M2_CLIENT_UPLOAD_STATUSES = {
    CaseStatus.M2_DESCRIPTION_PENDING,
    CaseStatus.M2_DOCUMENTS_OPTIONAL,
    CaseStatus.M2_SLOT_PENDING,
    CaseStatus.M2_PAYMENT_PENDING,
    CaseStatus.M2_CONSULTATION_BOOKED,
}
_UPLOAD_CALLBACKS = {
    "documents_upload_open",
    "doc_upload_ddu",
    "doc_upload_appendix",
    "doc_upload_additional",
    "doc_upload_act",
    "doc_upload_payment",
    "doc_upload_correspondence",
    "doc_upload_other",
}


def _case_status(case) -> CaseStatus | None:
    if case is None:
        return None
    if isinstance(case.status, CaseStatus):
        return case.status
    try:
        return CaseStatus(str(case.status))
    except (TypeError, ValueError):
        return None


def client_document_upload_allowed(case) -> bool:
    status = _case_status(case)
    route = str(getattr(case, "route", "") or "").upper()
    if route == "M1":
        return status in _M1_CLIENT_UPLOAD_STATUSES
    if route == "M2":
        return status in _M2_CLIENT_UPLOAD_STATUSES
    return False


def replacement_snapshot_matches(
    document,
    latest,
    *,
    case_id: int,
    document_type: str,
    document_id: int,
    expected_version: int,
) -> bool:
    if document is None or latest is None:
        return False
    try:
        return bool(
            int(document.id) == int(document_id)
            and int(document.case_id) == int(case_id)
            and str(document.document_type) == str(document_type)
            and int(document.version or 0) == int(expected_version)
            and str(document.status) in _REPLACEMENT_STATUSES
            and int(latest.id) == int(document.id)
        )
    except (TypeError, ValueError):
        return False


async def _answer_recovery(event, text: str) -> None:
    try:
        await event.answer(
            text,
            reply_markup=one(
                ("📄 Открыть актуальные документы", "documents_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
    except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
        logger.warning("Не удалось показать восстановление замены документа.")


async def _stage_recovery(event, state) -> None:
    if state is not None and hasattr(state, "clear"):
        await state.clear()
    text = (
        "ℹ️ Этап дела уже изменился. Эта загрузка больше не относится к текущему шагу, "
        "поэтому файл не обрабатывался и статус дела не менялся.\n\n"
        "Откройте «Моё дело» или актуальный раздел документов — там показано допустимое действие."
    )
    markup = one(
        ("📁 Открыть текущее дело", "my_case_open"),
        ("📄 Актуальные документы", "documents_open"),
        ("🏠 Главная", "nav_home"),
    )
    try:
        if isinstance(event, CallbackQuery):
            try:
                await event.message.edit_text(text, reply_markup=markup)
            except TelegramBadRequest as error:
                if "message is not modified" not in str(error).lower():
                    await event.message.answer(text, reply_markup=markup)
            await event.answer("Этап дела изменился.")
        else:
            await event.answer(text, reply_markup=markup)
    except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
        logger.warning("Не удалось показать восстановление загрузки после смены этапа.")


class ClientDocumentUploadStageProtectionMiddleware:
    """Fail closed when an old client upload action outlives its legal stage.

    Telegram messages and inline keyboards can remain visible for a long time.
    Every generic upload callback and the final file message therefore re-check
    the active case immediately before entering the encrypted upload pipeline.
    This prevents stale screens from attaching new evidence to contract,
    payment, POA, court, enforcement, completed-consultation or closed stages.
    """

    @staticmethod
    def _is_upload_callback(event: CallbackQuery) -> bool:
        value = str(event.data or "")
        return bool(
            value in _UPLOAD_CALLBACKS
            or value.startswith("doc_type:")
            or value.startswith("document_reupload:")
        )

    async def __call__(self, handler, event, data):
        state = data.get("state")
        targeted = False
        if isinstance(event, CallbackQuery):
            targeted = self._is_upload_callback(event)
        elif isinstance(event, Message) and state is not None:
            current_state = await state.get_state()
            targeted = bool(
                current_state == DocumentUploadStates.waiting_file.state
                and (getattr(event, "document", None) or getattr(event, "photo", None))
            )
        if not targeted:
            return await handler(event, data)

        db = data.get("db")
        if db is None:
            logger.error("Client document stage guard has no database session")
            await _stage_recovery(event, state)
            return None

        try:
            ctx = BotContextService(db)
            if isinstance(event, CallbackQuery):
                user = await ctx.get_user_from_callback(event)
            else:
                user = await ctx.get_user_from_message(event)
            case = await ctx.case_service.get_active_case_for_user(user.id)
        except Exception:
            logger.exception("Не удалось проверить этап дела перед загрузкой документа.")
            await db.rollback()
            await _stage_recovery(event, state)
            return None

        if case is None or not client_document_upload_allowed(case):
            await db.rollback()
            await _stage_recovery(event, state)
            return None

        return await handler(event, data)


class DocumentReplacementUploadProtectionMiddleware:
    """Re-check a direct replacement snapshot when the replacement file arrives.

    The inline action center validates the snapshot when the client opens the
    replacement flow. This middleware validates it again at message receipt,
    immediately before the legacy encrypted upload pipeline starts. It keeps a
    stale lawyer decision from causing an unnecessary file download or a new
    version based on an obsolete replacement request.
    """

    async def __call__(self, handler, event, data):
        state = data.get("state")
        if state is None or not hasattr(state, "get_data"):
            return await handler(event, data)

        current_state = await state.get_state()
        if current_state != DocumentUploadStates.waiting_file.state:
            return await handler(event, data)

        state_data = await state.get_data()
        has_replacement_marker = (
            "replacement_document_id" in state_data
            or "replacement_expected_version" in state_data
        )
        if not has_replacement_marker:
            return await handler(event, data)

        if not getattr(event, "document", None) and not getattr(event, "photo", None):
            return await handler(event, data)

        try:
            document_id = int(state_data.get("replacement_document_id"))
            expected_version = int(state_data.get("replacement_expected_version"))
            document_type = str(state_data.get("document_type") or "").strip()
            if document_id <= 0 or expected_version <= 0 or not document_type:
                raise ValueError("invalid replacement snapshot")
        except (TypeError, ValueError):
            await state.clear()
            await _answer_recovery(
                event,
                "⚠️ Запрос на замену документа повреждён или устарел. Файл не обрабатывался. Откройте актуальные документы и повторите действие.",
            )
            return None

        db = data.get("db")
        if db is None:
            logger.error("Replacement upload guard has no database session")
            await _answer_recovery(
                event,
                "⚠️ Не удалось проверить актуальность запроса юриста. Файл не обрабатывался. Повторите отправку чуть позже.",
            )
            return None

        try:
            ctx = BotContextService(db)
            user = await ctx.get_user_from_message(event)
            case = await ctx.case_service.get_active_case_for_user(user.id)
            if case is None:
                await state.clear()
                await _answer_recovery(
                    event,
                    "Активное дело больше не найдено. Файл не обрабатывался.",
                )
                return None

            document = await db.get(Document, document_id)
            latest = (
                await db.execute(
                    select(Document)
                    .where(Document.case_id == case.id)
                    .where(Document.document_type == document_type)
                    .where(Document.status != _ARCHIVED_STATUS)
                    .order_by(Document.version.desc(), Document.id.desc())
                    .limit(1)
                )
            ).scalars().first()
        except Exception:
            logger.exception("Не удалось повторно проверить snapshot замены документа.")
            await _answer_recovery(
                event,
                "⚠️ Не удалось подтвердить актуальность запроса юриста. Файл не обрабатывался и не потерян у вас. Повторите отправку позже или откройте документы.",
            )
            return None

        if not replacement_snapshot_matches(
            document,
            latest,
            case_id=case.id,
            document_type=document_type,
            document_id=document_id,
            expected_version=expected_version,
        ):
            await state.clear()
            await _answer_recovery(
                event,
                "ℹ️ Запрос юриста уже изменился: эта версия больше не требует замены или появилась новая версия. Файл не обрабатывался. Откройте актуальные документы.",
            )
            return None

        return await handler(event, data)
