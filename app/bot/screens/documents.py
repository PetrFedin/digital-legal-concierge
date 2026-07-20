import logging
from pathlib import Path

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import DocumentUploadStates
from app.domain.consultations.consultation_service import (
    ActiveConsultationConflictError,
    ConsultationNotFoundError,
    ConsultationService,
)
from app.domain.consultations.state_machine import InvalidConsultationTransition
from app.domain.documents.document_service import DocumentService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.document_statuses import DocumentStatus
from app.models.consultation import Consultation
from app.models.document import Document
from app.storage import LocalStorageService

router = Router()
logger = logging.getLogger(__name__)

TYPES = [
    ("ДДУ", "DDU"),
    ("Приложение", "APPENDIX"),
    ("Допсоглашение", "ADDITIONAL_AGREEMENT"),
    ("Акт", "TRANSFER_ACT"),
    ("Платежные документы", "PAYMENT_PROOF"),
    ("Переписка", "CORRESPONDENCE"),
    ("Другой документ", "OTHER"),
]

DOC_UPLOAD_ALIASES = {
    "doc_upload_ddu": "DDU",
    "doc_upload_appendix": "APPENDIX",
    "doc_upload_additional": "ADDITIONAL_AGREEMENT",
    "doc_upload_act": "TRANSFER_ACT",
    "doc_upload_payment": "PAYMENT_PROOF",
    "doc_upload_correspondence": "CORRESPONDENCE",
    "doc_upload_other": "OTHER",
}

M2_DOCUMENTS_ERROR_TEXT = (
    "Не удалось обработать документы. Попробуйте ещё раз немного позже."
)
M2_DOCUMENTS_COMPLETED_TEXT = (
    "Документный шаг уже завершён. "
    "Продолжите оформление в разделе «Моё дело»."
)
M2_DOCUMENTS_INSTRUCTION = (
    "Приложите документы, которые помогут юристу подготовиться.\n\n"
    "Можно отправить несколько файлов по одному. После загрузки нажмите "
    "«Перейти дальше».\n\n"
    "Не отправляйте пароли, данные банковских карт и другие секретные сведения."
)
class M2DocumentError(ValueError):
    """A Telegram file cannot be safely stored for the M2 document step."""


M2_DOCUMENTS_DOMAIN_ERRORS = (
    ConsultationNotFoundError,
    ActiveConsultationConflictError,
    InvalidConsultationTransition,
    M2DocumentError,
)


def _m2_step_keyboard():
    return one(
        ("📎 Приложить документы", "m2_documents_open"),
        ("⏭ Пропустить", "m2_documents_skip"),
        ("🏠 Главная", "nav_home"),
    )


def _m2_uploaded_keyboard():
    return one(
        ("📎 Добавить ещё", "m2_documents_open"),
        ("➡️ Перейти дальше", "m2_documents_finish"),
        ("🏠 Главная", "nav_home"),
    )


def _m2_completed_keyboard():
    return one(("📁 Моё дело", "my_case_open"), ("🏠 Главная", "nav_home"))


def _case_belongs_to_user(case, user) -> bool:
    return bool(case and case.client_id == user.id)


def _log_unexpected(message: str, error: Exception) -> None:
    redacted_error = RuntimeError("exception details redacted")
    logger.error(
        message,
        exc_info=(RuntimeError, redacted_error, error.__traceback__),
    )


async def _load_m2_entities(db, user):
    ctx = BotContextService(db)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not _case_belongs_to_user(case, user):
        raise ConsultationNotFoundError("Активное дело клиента не найдено.")
    if case.route not in {RouteCode.M2, RouteCode.M2.value}:
        raise ConsultationNotFoundError("Документный шаг не относится к маршруту М2.")

    result = await db.execute(
        select(Consultation)
        .where(Consultation.case_id == case.id)
        .where(
            Consultation.status.notin_(ConsultationService.INACTIVE_STATUSES)
        )
        .order_by(Consultation.created_at.desc(), Consultation.id.desc())
    )
    active = list(result.scalars().all())
    if not active:
        raise ConsultationNotFoundError("Активная консультация клиента не найдена.")
    if len(active) > 1:
        raise ActiveConsultationConflictError(
            "Для дела найдено несколько активных консультаций."
        )
    consultation = active[0]
    if consultation.case_id != case.id:
        raise ConsultationNotFoundError("Консультация не принадлежит делу клиента.")
    return case, consultation


def _validate_m2_fsm(data, case, consultation, *, required: bool) -> None:
    if required and data.get("m2_documents_flow") is not True:
        raise ConsultationNotFoundError("Сессия загрузки документов не найдена.")
    if data.get("case_id") is not None and data.get("case_id") != case.id:
        raise ConsultationNotFoundError("FSM относится к другому делу.")
    if (
        data.get("consultation_id") is not None
        and data.get("consultation_id") != consultation.id
    ):
        raise ConsultationNotFoundError("FSM относится к другой консультации.")
    if required and (
        data.get("case_id") != case.id
        or data.get("consultation_id") != consultation.id
    ):
        raise ConsultationNotFoundError("FSM не подтверждает документный шаг.")


async def _show_m2_completed(callback, state):
    data = await state.get_data()
    if data.get("m2_documents_flow") is True:
        await state.clear()
    await callback.message.edit_text(
        M2_DOCUMENTS_COMPLETED_TEXT,
        reply_markup=_m2_completed_keyboard(),
    )


async def _show_m2_not_ready(callback):
    await callback.message.edit_text(
        "Сначала сохраните описание ситуации для юриста.",
        reply_markup=_m2_completed_keyboard(),
    )


@router.callback_query(lambda c: c.data == "m2_documents_open")
async def open_m2_documents(callback: CallbackQuery, state: FSMContext, db):
    try:
        ctx = BotContextService(db)
        user = await ctx.get_user_from_callback(callback)
        case, consultation = await _load_m2_entities(db, user)
        status = ConsultationStatus(consultation.status)
        if status == ConsultationStatus.DESCRIPTION_PENDING:
            await _show_m2_not_ready(callback)
            return
        if status != ConsultationStatus.DOCUMENTS_OPTIONAL:
            await _show_m2_completed(callback, state)
            return

        current_data = await state.get_data()
        processed_file_keys = (
            list(current_data.get("processed_file_keys", []))
            if current_data.get("m2_documents_flow") is True
            and current_data.get("case_id") == case.id
            and current_data.get("consultation_id") == consultation.id
            else []
        )
        await state.clear()
        await state.update_data(
            case_id=case.id,
            consultation_id=consultation.id,
            m2_documents_flow=True,
            document_type="OTHER",
            processed_file_keys=processed_file_keys,
        )
        await state.set_state(DocumentUploadStates.waiting_file)
    except M2_DOCUMENTS_DOMAIN_ERRORS:
        await db.rollback()
        await callback.message.edit_text(
            M2_DOCUMENTS_ERROR_TEXT,
            reply_markup=_m2_completed_keyboard(),
        )
        return
    except Exception as error:
        await db.rollback()
        _log_unexpected("Unexpected error while opening Telegram M2 documents", error)
        await callback.message.edit_text(
            M2_DOCUMENTS_ERROR_TEXT,
            reply_markup=_m2_completed_keyboard(),
        )
        return

    await callback.message.edit_text(
        M2_DOCUMENTS_INSTRUCTION,
        reply_markup=one(
            ("⏭ Пропустить", "m2_documents_skip"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "documents_open")
async def docs(callback: CallbackQuery, db, state: FSMContext):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text(
            "📄 Документы доступны после создания обращения.",
            reply_markup=one(("🧮 Рассчитать", "calc_start"), ("💬 Юрист", "calc_to_m2")),
        )
        return
    if case.route in {RouteCode.M2, RouteCode.M2.value}:
        await open_m2_documents(callback, state, db)
        return

    items = [(f"Загрузить {title}", f"doc_type:{code}") for title, code in TYPES]
    items += [
        ("📋 Список документов", "documents_list_open"),
        ("Я загрузил все документы", "doc_finish_upload"),
        ("📁 Мое дело", "my_case_open"),
    ]
    await callback.message.edit_text(
        "📄 Документы\n\nЗагрузите копии или сканы. Для М1 минимум нужен ДДУ.",
        reply_markup=one(*items),
    )


@router.callback_query(
    lambda c: c.data.startswith("doc_type:") or c.data in DOC_UPLOAD_ALIASES
)
async def choose(callback: CallbackQuery, state: FSMContext, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case and case.route in {RouteCode.M2, RouteCode.M2.value}:
        await open_m2_documents(callback, state, db)
        return
    document_type = (
        callback.data.split(":", 1)[1]
        if callback.data.startswith("doc_type:")
        else DOC_UPLOAD_ALIASES[callback.data]
    )
    await state.update_data(document_type=document_type)
    await state.set_state(DocumentUploadStates.waiting_file)
    await callback.message.edit_text("Прикрепите PDF/JPG/PNG файлом или изображением.")


def _telegram_file_data(message):
    if message.document:
        file_id = message.document.file_id
        file_key = getattr(message.document, "file_unique_id", None) or file_id
        if not file_id or not file_key:
            raise M2DocumentError("Telegram document has no reusable file identifier.")
        return (
            message.document.file_name or "document.bin",
            message.document.mime_type,
            message.document.file_size,
            file_id,
            file_key,
        )
    photo = message.photo[-1]
    file_id = photo.file_id
    file_key = getattr(photo, "file_unique_id", None) or file_id
    if not file_id or not file_key:
        raise M2DocumentError("Telegram photo has no reusable file identifier.")
    return (
        f"photo_{getattr(photo, 'file_unique_id', None) or 'telegram'}.jpg",
        "image/jpeg",
        photo.file_size,
        file_id,
        file_key,
    )


def _remove_current_stored_file(stored) -> None:
    if stored is None:
        return
    try:
        Path(stored.storage_path).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove an orphaned Telegram document file")


async def _upload_m2_document(message, state, db, data):
    stored = None
    committed = False
    try:
        ctx = BotContextService(db)
        user = await ctx.get_user_from_message(message)
        case, consultation = await _load_m2_entities(db, user)
        _validate_m2_fsm(data, case, consultation, required=True)
        if ConsultationStatus(consultation.status) != ConsultationStatus.DOCUMENTS_OPTIONAL:
            raise InvalidConsultationTransition(
                "Документный шаг консультации уже завершён."
            )
        name, mime, size, file_id, file_key = _telegram_file_data(message)
        processed_file_keys = list(data.get("processed_file_keys", []))
        if file_key in processed_file_keys:
            await message.answer(
                "Этот документ уже сохранён.",
                reply_markup=_m2_uploaded_keyboard(),
            )
            return
        try:
            stored = await LocalStorageService().save_telegram_file(
                bot=message.bot,
                telegram_file_id=file_id,
                case_id=case.id,
                original_name=name,
                mime_type=mime,
                file_size=size,
            )
        except (OSError, TelegramAPIError) as error:
            raise M2DocumentError("Telegram file could not be stored.") from error
        if not Path(stored.storage_path).is_file():
            raise M2DocumentError("Stored Telegram file is unavailable.")
        await DocumentService(db).create_document(
            case=case,
            uploaded_by_user_id=user.id,
            document_type=data.get("document_type", "OTHER"),
            file_name=stored.original_name,
            file_path=stored.storage_path,
            mime_type=stored.mime_type,
            file_size=stored.file_size,
        )
        await db.commit()
        committed = True
    except M2_DOCUMENTS_DOMAIN_ERRORS:
        if not committed:
            await db.rollback()
            _remove_current_stored_file(stored)
        await message.answer(M2_DOCUMENTS_ERROR_TEXT)
        return
    except Exception as error:
        if not committed:
            await db.rollback()
            _remove_current_stored_file(stored)
        _log_unexpected("Unexpected error while saving a Telegram M2 document", error)
        await message.answer(M2_DOCUMENTS_ERROR_TEXT)
        return

    processed_file_keys.append(file_key)
    try:
        await state.update_data(processed_file_keys=processed_file_keys)
    except Exception as error:
        _log_unexpected(
            "Could not update Telegram M2 document idempotency state after commit",
            error,
        )
    try:
        await message.answer(
            "Документ сохранён.",
            reply_markup=_m2_uploaded_keyboard(),
        )
    except Exception as error:
        _log_unexpected(
            "Could not send Telegram M2 document success response after commit",
            error,
        )


@router.message(DocumentUploadStates.waiting_file)
async def upload(message: Message, state: FSMContext, db):
    data = await state.get_data()
    if not message.document and not message.photo:
        if data.get("m2_documents_flow"):
            await message.answer(
                "Отправьте документ файлом или используйте кнопку «Пропустить».",
                reply_markup=one(
                    ("⏭ Пропустить", "m2_documents_skip"),
                    ("🏠 Главная", "nav_home"),
                ),
            )
        else:
            await message.answer("⚠️ Прикрепите файл или изображение.")
        return

    if data.get("m2_documents_flow"):
        await _upload_m2_document(message, state, db, data)
        return

    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await message.answer("Нет активного дела. Начните с расчета или консультации.")
        await state.clear()
        return
    if case.route in {RouteCode.M2, RouteCode.M2.value}:
        await db.rollback()
        await message.answer(M2_DOCUMENTS_ERROR_TEXT)
        return
    document_type = data["document_type"]
    name, mime, size, file_id, _ = _telegram_file_data(message)
    try:
        stored = await LocalStorageService().save_telegram_file(
            bot=message.bot,
            telegram_file_id=file_id,
            case_id=case.id,
            original_name=name,
            mime_type=mime,
            file_size=size,
        )
    except Exception:
        # Existing M1 compatibility: keep file_id when Telegram download is unavailable.
        stored = type(
            "Stored",
            (),
            {
                "original_name": name,
                "storage_path": file_id,
                "mime_type": mime,
                "file_size": size,
            },
        )()

    document = await DocumentService(db).create_document(
        case=case,
        uploaded_by_user_id=user.id,
        document_type=document_type,
        file_name=stored.original_name,
        file_path=stored.storage_path,
        mime_type=stored.mime_type,
        file_size=stored.file_size,
    )
    await db.commit()
    await state.clear()
    await message.answer(
        f"✅ Документ загружен: {document.title}, версия {document.version}",
        reply_markup=one(
            ("Загрузить еще", "documents_open"),
            ("📋 Список", "documents_list_open"),
            ("Я загрузил все документы", "doc_finish_upload"),
            ("📁 Мое дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: c.data == "documents_list_open")
async def list_docs(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    docs = await DocumentService(db).list_case_documents(case.id) if case else []
    text = "📋 Список документов\n\n" + (
        "Пока пусто."
        if not docs
        else "\n".join(
            [f"#{doc.id} {doc.title} — {doc.status}, v{doc.version}" for doc in docs]
        )
    )
    await callback.message.edit_text(
        text,
        reply_markup=one(
            ("Загрузить документ", "documents_open"),
            ("📁 Мое дело", "my_case_open"),
        ),
    )


async def _complete_m2_documents(callback, state, db, *, documents_uploaded: bool):
    try:
        ctx = BotContextService(db)
        user = await ctx.get_user_from_callback(callback)
        case, consultation = await _load_m2_entities(db, user)
        status = ConsultationStatus(consultation.status)
        if status == ConsultationStatus.DESCRIPTION_PENDING:
            raise InvalidConsultationTransition(
                "Документный шаг консультации ещё не начат."
            )
        if status != ConsultationStatus.DOCUMENTS_OPTIONAL:
            await _show_m2_completed(callback, state)
            return

        data = await state.get_data()
        _validate_m2_fsm(
            data,
            case,
            consultation,
            required=documents_uploaded,
        )
        if documents_uploaded:
            documents_count = (
                await db.execute(
                    select(func.count(Document.id)).where(
                        Document.case_id == case.id,
                        Document.uploaded_by_user_id == user.id,
                        Document.status != DocumentStatus.ARCHIVED.value,
                    )
                )
            ).scalar_one()
            if not documents_count:
                await callback.message.edit_text(
                    "Сначала приложите хотя бы один документ или пропустите этот шаг.",
                    reply_markup=one(
                        ("📎 Приложить документы", "m2_documents_open"),
                        ("⏭ Пропустить", "m2_documents_skip"),
                        ("🏠 Главная", "nav_home"),
                    ),
                )
                return

        await ConsultationService(db).complete_documents_step(
            consultation=consultation,
            case=case,
            documents_uploaded=documents_uploaded,
            actor_type="client",
            actor_id=user.id,
            source="telegram",
        )
        await db.commit()
    except M2_DOCUMENTS_DOMAIN_ERRORS:
        await db.rollback()
        await callback.message.edit_text(
            M2_DOCUMENTS_ERROR_TEXT,
            reply_markup=_m2_step_keyboard(),
        )
        return
    except Exception as error:
        await db.rollback()
        _log_unexpected(
            "Unexpected error while completing Telegram M2 documents",
            error,
        )
        await callback.message.edit_text(
            M2_DOCUMENTS_ERROR_TEXT,
            reply_markup=_m2_step_keyboard(),
        )
        return

    await state.clear()
    text = (
        "Документы добавлены.\n\n"
        "Следующий шаг — выбрать удобное время консультации."
        if documents_uploaded
        else "Шаг с документами пропущен.\n\n"
        "Следующий шаг — выбрать удобное время консультации."
    )
    await callback.message.edit_text(text, reply_markup=_m2_completed_keyboard())


@router.callback_query(lambda c: c.data == "m2_documents_finish")
async def finish_m2_documents(callback: CallbackQuery, state: FSMContext, db):
    await _complete_m2_documents(
        callback,
        state,
        db,
        documents_uploaded=True,
    )


@router.callback_query(lambda c: c.data == "m2_documents_skip")
async def skip_m2_documents(callback: CallbackQuery, state: FSMContext, db):
    await _complete_m2_documents(
        callback,
        state,
        db,
        documents_uploaded=False,
    )


@router.callback_query(lambda c: c.data == "doc_finish_upload")
async def finish(callback: CallbackQuery, db, state: FSMContext):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case and case.route in {RouteCode.M2, RouteCode.M2.value}:
        await finish_m2_documents(callback, state, db)
        return
    await DocumentService(db).send_documents_to_review(case=case, actor_id=user.id)
    if case.route in {RouteCode.M1, RouteCode.M1.value}:
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.M1_LAWYER_REVIEW,
            actor_type="client",
            actor_id=user.id,
            force=True,
            comment="Документы переданы юристу",
        )
        message = "✅ Документы переданы на проверку юристу."
    else:
        # Preserve the legacy behavior for route-less historical cases.
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.M2_SLOT_PENDING,
            actor_type="client",
            actor_id=user.id,
            force=True,
            comment="Документы М2 сохранены",
        )
        message = "✅ Документы сохранены. Теперь выберите время консультации."
    await db.commit()
    await callback.message.edit_text(
        message,
        reply_markup=one(
            ("📁 Мое дело", "my_case_open"),
            ("📅 Выбрать время", "consult_slot_open"),
        ),
    )


@router.callback_query(lambda c: c.data == "doc_skip_m2")
async def skip(callback: CallbackQuery, db, state: FSMContext):
    await skip_m2_documents(callback, state, db)
