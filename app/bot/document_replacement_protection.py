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
    "doc_finish_upload",
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
        "ℹ️ Этап дела или само обращение уже изменились. Эта загрузка или передача файлов больше не относится к текущему шагу, "
        "поэтому файл не обрабатывался, документы не передавались и статус дела не менялся.\n\n"
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


async def _draft_target_case(db, ctx, *, user_id: int, state_data: dict):
    """Resolve the server-verified Case that owns an armed upload draft.

    Generic uploads carry an exact ``document_case_id``. A lawyer-requested
    replacement carries the immutable Document id/version snapshot, so its Case
    is derived from the persisted Document rather than trusting client state.
    Ownership is rechecked through CaseService in both paths.
    """

    replacement_document_id = state_data.get("replacement_document_id")
    if replacement_document_id is not None:
        try:
            document_id = int(replacement_document_id)
        except (TypeError, ValueError):
            return None
        if document_id <= 0:
            return None
        document = await db.get(Document, document_id)
        if document is None:
            return None
        return await ctx.case_service.get_case_for_user(
            user_id=int(user_id),
            case_id=int(document.case_id),
        )

    try:
        case_id = int(state_data.get("document_case_id"))
    except (TypeError, ValueError):
        return None
    if case_id <= 0:
        return None
    return await ctx.case_service.get_case_for_user(
        user_id=int(user_id),
        case_id=case_id,
    )


async def _preserve_switched_case_upload(
    event: Message,
    *,
    case_id: int,
    case_number: str,
) -> None:
    """Keep the draft armed while refusing to process a file in another Case."""

    text = (
        "ℹ️ Файл не обрабатывался и не сохранялся: сейчас открыто другое обращение.\n\n"
        f"Черновик загрузки для обращения № {case_number} сохранён вместе с выбранным типом документа. "
        "Вернитесь к нему одной кнопкой, затем отправьте тот же файл ещё раз."
    )
    markup = one(
        (
            f"↩️ Вернуться к обращению № {case_number}",
            f"document_upload_resume:v2:{int(case_id)}",
        ),
        ("Отменить загрузку", "nav_cancel"),
        ("🏠 Главная", "nav_home"),
    )
    try:
        await event.answer(text, reply_markup=markup)
    except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
        logger.warning("Не удалось показать восстановление Case-bound загрузки документа.")


class ClientDocumentUploadStageProtectionMiddleware:
    """Fail closed without discarding a still-valid Case-bound upload draft.

    Telegram messages and inline keyboards can remain visible for a long time.
    Every generic upload callback, final document handoff callback and final file
    message therefore re-checks Case/stage immediately before encrypted upload or
    review. A file sent while another active Case is selected is never downloaded
    or stored there: the exact original draft remains armed and the client gets a
    one-tap return action. The draft is cleared only when its original Case or
    legal stage is genuinely no longer valid.
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
        is_file_message = False
        if isinstance(event, CallbackQuery):
            targeted = self._is_upload_callback(event)
        elif isinstance(event, Message) and state is not None:
            current_state = await state.get_state()
            is_file_message = bool(
                current_state == DocumentUploadStates.waiting_file.state
                and (getattr(event, "document", None) or getattr(event, "photo", None))
            )
            targeted = is_file_message
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
        except Exception:
            logger.exception("Не удалось определить клиента перед загрузкой/передачей документа.")
            await db.rollback()
            await _stage_recovery(event, state)
            return None

        if is_file_message:
            try:
                state_data = await state.get_data()
                target_case = await _draft_target_case(
                    db,
                    ctx,
                    user_id=int(user.id),
                    state_data=state_data,
                )
            except Exception:
                logger.exception("Не удалось проверить Case-bound snapshot загрузки документа")
                await db.rollback()
                await _stage_recovery(event, state)
                return None

            if target_case is None or not client_document_upload_allowed(target_case):
                await db.rollback()
                await _stage_recovery(event, state)
                return None

            try:
                selected_case = await ctx.case_service.get_active_case_for_user(user.id)
            except Exception:
                logger.exception("Не удалось проверить выбранное дело перед загрузкой документа")
                await db.rollback()
                await _stage_recovery(event, state)
                return None

            if selected_case is None or int(selected_case.id) != int(target_case.id):
                # Snapshot presentation values before rollback: ORM instances may
                # expire at the transaction boundary even with expire_on_commit=False.
                target_case_id = int(target_case.id)
                target_case_number = str(target_case.case_number)
                await db.rollback()
                await _preserve_switched_case_upload(
                    event,
                    case_id=target_case_id,
                    case_number=target_case_number,
                )
                return None

            # The exact selected Case is also the server-verified draft owner.
            # The stricter replacement middleware below will additionally check
            # document id/version/status for lawyer-requested replacements.
            return await handler(event, data)

        try:
            case = await ctx.case_service.get_active_case_for_user(user.id)
        except Exception:
            logger.exception("Не удалось проверить этап дела перед документным действием.")
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
