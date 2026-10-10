import logging
from collections.abc import Iterable

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import DocumentUploadStates
from app.domain.documents.document_service import (
    DocumentSecurityPendingError,
    DocumentService,
    DocumentsAlreadySubmittedError,
    DuplicateDocumentError,
    MissingRequiredDocumentsError,
)
from app.domain.statuses.case_statuses import CaseStatus
from app.security.file_uploads import UploadSecurityError, safe_filename
from app.storage import LocalStorageService

logger = logging.getLogger(__name__)
router = Router()

TYPES = [
    ("ДДУ", "DDU"),
    ("Приложение", "APPENDIX"),
    ("Допсоглашение", "ADDITIONAL_AGREEMENT"),
    ("Акт", "TRANSFER_ACT"),
    ("Платёжные документы", "PAYMENT_PROOF"),
    ("Переписка", "CORRESPONDENCE"),
    ("Другой документ", "OTHER"),
]
_DOCUMENT_TYPE_CODES = {code for _, code in TYPES}

DOC_UPLOAD_ALIASES = {
    "doc_upload_ddu": "DDU",
    "doc_upload_appendix": "APPENDIX",
    "doc_upload_additional": "ADDITIONAL_AGREEMENT",
    "doc_upload_act": "TRANSFER_ACT",
    "doc_upload_payment": "PAYMENT_PROOF",
    "doc_upload_correspondence": "CORRESPONDENCE",
    "doc_upload_other": "OTHER",
}

_M1_COLLECTION_STATUSES = {
    CaseStatus.M1_DOCUMENTS_PENDING,
    CaseStatus.M1_DOCUMENTS_RECEIVED,
    CaseStatus.M1_DOCS_REQUESTED,
}
_M2_CAN_SKIP_STATUSES = {
    CaseStatus.M2_DESCRIPTION_PENDING,
    CaseStatus.M2_DOCUMENTS_OPTIONAL,
    CaseStatus.M2_SLOT_PENDING,
}
_READ_ONLY_CASE_STATUSES = {
    CaseStatus.M1_CLOSED,
    CaseStatus.M2_CLOSED,
    CaseStatus.ARCHIVED,
}
_DOCUMENT_STATUS_LABELS = {
    "UPLOADED": "Безопасно загружен",
    "PENDING": "Статус уточняется",
    "PENDING_REVIEW": "Статус уточняется",
    "REVIEW_PENDING": "Статус уточняется",
    "REVIEW_REQUIRED": "Статус уточняется",
    "NEEDS_REVIEW": "Статус уточняется",
    "ON_REVIEW": "Проверяет юрист",
    "APPROVED": "Принят юристом",
    "ACCEPTED": "Принят юристом",
    "VERIFIED": "Принят юристом",
    "REJECTED": "Нужно заменить файл",
    "NEEDS_REUPLOAD": "Нужно загрузить новую версию",
    "ARCHIVED": "Предыдущая версия",
}
_DOCUMENT_REPLACEMENT_STATUSES = {"REJECTED", "NEEDS_REUPLOAD"}
_DOCUMENT_REVIEW_STATUSES = {"ON_REVIEW"}
_DOCUMENT_APPROVED_STATUSES = {"APPROVED", "ACCEPTED", "VERIFIED"}
_PAGE_SIZE = 8
_MAX_CLIENT_COMMENT_LENGTH = 240


def _case_status(case) -> CaseStatus:
    if isinstance(case.status, CaseStatus):
        return case.status
    return CaseStatus(str(case.status))


def _status(document) -> str:
    return str(document.status)


def _short_text(value: str, limit: int = _MAX_CLIENT_COMMENT_LENGTH) -> str:
    clean = " ".join(str(value or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(limit - 1, 1)].rstrip() + "…"


def _client_document_status(document) -> str:
    return _DOCUMENT_STATUS_LABELS.get(
        _status(document),
        "Безопасно загружен",
    )


def _client_document_comment(document) -> str | None:
    comment = _short_text(str(document.lawyer_comment or ""))
    if not comment:
        return None
    if _status(document) in _DOCUMENT_REPLACEMENT_STATUSES:
        return f"Что исправить: {comment}"
    return f"Комментарий юриста: {comment}"


def _active_documents(documents: Iterable) -> list:
    return [document for document in documents if _status(document) != "ARCHIVED"]


def _archived_documents(documents: Iterable) -> list:
    return [document for document in documents if _status(document) == "ARCHIVED"]


def _document_block(document, *, history: bool = False) -> str:
    heading = f"• {document.title}, версия {document.version}"
    lines = [heading, f"  {_client_document_status(document)}"]
    comment = _client_document_comment(document)
    if comment and not history:
        lines.append(f"  {comment}")
    return "\n".join(lines)


def _paginate(items: list, page: int) -> tuple[list, int, int]:
    total_pages = max(1, (len(items) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    current_page = min(max(int(page), 0), total_pages - 1)
    start = current_page * _PAGE_SIZE
    return items[start : start + _PAGE_SIZE], current_page, total_pages


def _page_navigation(prefix: str, page: int, total_pages: int):
    buttons: list[tuple[str, str]] = []
    if page > 0:
        buttons.append(("⬅️ Предыдущая страница", f"{prefix}:{page - 1}"))
    if page + 1 < total_pages:
        buttons.append(("Следующая страница ➡️", f"{prefix}:{page + 1}"))
    return buttons


async def _safe_edit(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup,
    unchanged_notice: str = "Статусы пока не изменились.",
) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
        await callback.answer(unchanged_notice)


async def _present_committed_result(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup,
    saved_notice: str,
) -> None:
    """Render a durable result without ever turning a UI failure into a write retry."""
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
        return
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            try:
                await callback.answer(saved_notice)
            except Exception:
                logger.warning("Saved document result callback acknowledgement failed")
            return
        logger.warning("Saved document result edit failed: %s", error)
    except Exception:
        logger.exception("Saved document result edit failed")

    try:
        await callback.message.answer(text, reply_markup=reply_markup)
        try:
            await callback.answer("Изменение сохранено. Результат открыт новым сообщением.")
        except Exception:
            logger.warning("Saved document result fallback acknowledgement failed")
    except Exception:
        logger.exception("Saved document result fallback message failed")
        try:
            await callback.answer(saved_notice, show_alert=True)
        except Exception:
            logger.warning("Saved document result final acknowledgement failed")


def _new_case_buttons() -> tuple[tuple[str, str], ...]:
    return (
        ("🧮 Рассчитать неустойку", "calc_start"),
        ("📅 Записаться на консультацию", "calc_to_m2"),
        ("🏠 Главная", "nav_home"),
    )


def _after_documents_buttons(case) -> tuple[tuple[str, str], ...]:
    status = _case_status(case)
    if status == CaseStatus.M2_CONSULTATION_BOOKED:
        return (
            ("👨‍⚖ Открыть консультацию", "consultation_booked_open"),
            ("📄 Документы", "documents_open"),
            ("✉️ Задать вопрос по делу", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    if status in {
        CaseStatus.M2_SLOT_PENDING,
        CaseStatus.M2_DOCUMENTS_OPTIONAL,
        CaseStatus.M2_DESCRIPTION_PENDING,
    }:
        return (
            ("📅 Выбрать время", "consult_slot_open"),
            ("📄 Документы", "documents_open"),
            ("✉️ Задать вопрос по делу", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    if status == CaseStatus.M2_PAYMENT_PENDING:
        return (
            ("Продолжить подтверждение", "consult_pay"),
            ("📄 Документы", "documents_open"),
            ("✉️ Задать вопрос по делу", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    return (
        ("📄 Документы", "documents_open"),
        ("✉️ Задать вопрос по делу", "message_create"),
        ("📁 Моё дело", "my_case_open"),
        ("🏠 Главная", "nav_home"),
    )


def _document_counts(documents: list) -> dict[str, int]:
    return {
        "new": sum(_status(item) == "UPLOADED" for item in documents),
        "review": sum(_status(item) in _DOCUMENT_REVIEW_STATUSES for item in documents),
        "approved": sum(
            _status(item) in _DOCUMENT_APPROVED_STATUSES for item in documents
        ),
        "replacement": sum(
            _status(item) in _DOCUMENT_REPLACEMENT_STATUSES for item in documents
        ),
    }


def _recommended_step(case, documents: list) -> tuple[str, list[tuple[str, str]]]:
    if _case_status(case) in _READ_ONLY_CASE_STATUSES:
        return (
            "Дело закрыто. Документы доступны только для просмотра.",
            [],
        )
    counts = _document_counts(documents)
    if counts["replacement"]:
        return (
            "Загрузите новую версию файла с учётом комментария юриста.",
            [("🔁 Загрузить новую версию", "documents_upload_open")],
        )
    if counts["new"]:
        return (
            "Передайте новые файлы юристу. До передачи они остаются только в вашем деле.",
            [("✅ Передать новые файлы юристу", "doc_finish_upload")],
        )
    if not documents and case.route == "M2" and _case_status(case) in _M2_CAN_SKIP_STATUSES:
        return (
            "Документы необязательны. Можно добавить файл или перейти к выбору времени.",
            [
                ("Продолжить без документов", "doc_skip_m2"),
                ("➕ Добавить документ", "documents_upload_open"),
            ],
        )
    if not documents:
        return (
            "Загрузите ДДУ — без него дело нельзя передать юристу.",
            [("➕ Загрузить документ", "documents_upload_open")],
        )
    if counts["review"]:
        return (
            "Действий не требуется: юрист проверяет переданные файлы.",
            [("🔄 Обновить статусы", "documents_open")],
        )
    if counts["approved"] == len(documents):
        return (
            "Все актуальные документы приняты. Новый файл можно добавить при необходимости.",
            [("➕ Добавить документ", "documents_upload_open")],
        )
    return (
        "Проверьте актуальные документы и добавьте недостающий файл.",
        [("➕ Добавить документ", "documents_upload_open")],
    )


async def _load_case_documents(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text(
            "📄 Документы можно добавить после создания обращения.\n\n"
            "Начните с расчёта неустойки или записи на консультацию.",
            reply_markup=one(*_new_case_buttons()),
        )
        return None, []
    documents = await DocumentService(db).list_case_documents(case.id)
    return case, documents


async def _render_documents_home(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        latest = await ctx.case_service.get_latest_case_for_user(user.id)
        if latest and _case_status(latest) in _READ_ONLY_CASE_STATUSES:
            case = latest
            documents = await DocumentService(db).list_case_documents(case.id)
        else:
            await callback.message.edit_text(
                "📄 Документы можно добавить после создания обращения.\n\n"
                "Начните с расчёта неустойки или записи на консультацию.",
                reply_markup=one(*_new_case_buttons()),
            )
            return
    else:
        documents = await DocumentService(db).list_case_documents(case.id)

    active = _active_documents(documents)
    archived = _archived_documents(documents)
    counts = _document_counts(active)
    next_step, primary_buttons = _recommended_step(case, active)

    summary = (
        f"Актуальные: {len(active)} · готово к передаче: {counts['new']} · "
        f"на проверке: {counts['review']} · принято: {counts['approved']}"
    )
    preview_items = active[:4]
    preview = (
        "\n\n".join(_document_block(item) for item in preview_items)
        if preview_items
        else "Пока документов нет."
    )
    if len(active) > len(preview_items):
        preview += f"\n\nЕщё актуальных документов: {len(active) - len(preview_items)}."

    buttons = list(primary_buttons)
    primary_callbacks = {callback_data for _, callback_data in primary_buttons}
    read_only = _case_status(case) in _READ_ONLY_CASE_STATUSES
    if active:
        buttons.append(("📋 Все актуальные документы", "documents_list_open"))
    if (
        not read_only
        and "documents_upload_open" not in primary_callbacks
        and not counts["replacement"]
    ):
        buttons.append(("➕ Добавить документ", "documents_upload_open"))
    if archived:
        buttons.append((f"🕘 История версий ({len(archived)})", "documents_history_open"))
    if not read_only:
        buttons.append(("✉️ Задать вопрос по делу", "message_create"))
    buttons.extend(
        [
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ]
    )

    await _safe_edit(
        callback,
        "📄 Документы\n\n"
        f"{summary}\n\n"
        f"{preview}\n\n"
        f"Следующий шаг: {next_step}",
        reply_markup=one(*buttons),
    )


@router.callback_query(lambda c: c.data == "documents_open")
async def docs(callback: CallbackQuery, db):
    await _render_documents_home(callback, db)


@router.callback_query(lambda c: c.data == "documents_upload_open")
async def upload_menu(callback: CallbackQuery, state: FSMContext, db):
    case, _ = await _load_case_documents(callback, db)
    if not case:
        await state.clear()
        return

    await state.clear()
    await state.set_state(DocumentUploadStates.choosing_type)

    items = [(f"Загрузить: {title}", f"doc_type:{code}") for title, code in TYPES]
    if case.route == "M2" and _case_status(case) in _M2_CAN_SKIP_STATUSES:
        items.append(("Продолжить без документов", "doc_skip_m2"))
    items.extend(
        [
            ("⬅️ К обзору документов", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ]
    )

    requirement = (
        "Для передачи дела на проверку обязательно загрузите актуальный ДДУ."
        if case.route == "M1"
        else "Для консультации документы необязательны, но помогут юристу подготовиться."
    )
    await callback.message.edit_text(
        "➕ Добавить документ\n\n"
        "Выберите тип, затем прикрепите PDF, DOCX, JPG или PNG. Каждый файл "
        "проверяется и защищается до сохранения.\n\n"
        f"{requirement}",
        reply_markup=one(*items),
    )


@router.callback_query(
    lambda c: c.data.startswith("doc_type:") or c.data in DOC_UPLOAD_ALIASES
)
async def choose(callback: CallbackQuery, state: FSMContext):
    document_type = (
        callback.data.split(":", 1)[1]
        if callback.data.startswith("doc_type:")
        else DOC_UPLOAD_ALIASES[callback.data]
    )
    if document_type not in _DOCUMENT_TYPE_CODES:
        await state.clear()
        await state.set_state(DocumentUploadStates.choosing_type)
        await callback.message.edit_text(
            "Этот тип документа больше недоступен. Откройте список типов и выберите актуальный вариант.",
            reply_markup=one(
                ("Выбрать тип документа", "documents_upload_open"),
                ("📄 Обзор документов", "documents_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await state.update_data(document_type=document_type)
    await state.set_state(DocumentUploadStates.waiting_file)
    await callback.message.edit_text(
        "Прикрепите PDF, DOCX, JPG или PNG.\n\n"
        "После проверки бот подтвердит, что файл безопасно загружен.",
        reply_markup=one(
            ("Выбрать другой тип", "documents_upload_open"),
            ("Отменить действие", "nav_cancel"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.message(DocumentUploadStates.waiting_file)
async def upload(message: Message, state: FSMContext, db):
    if not message.document and not message.photo:
        await message.answer(
            "⚠️ Прикрепите файл или изображение.",
            reply_markup=one(
                ("Выбрать другой тип", "documents_upload_open"),
                ("Отменить действие", "nav_cancel"),
            ),
        )
        return

    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await state.clear()
        await message.answer(
            "Активное дело больше не найдено. Файл не загружен.",
            reply_markup=one(*_new_case_buttons()),
        )
        return

    data = await state.get_data()
    document_type = data.get("document_type")
    if not document_type:
        await state.clear()
        await message.answer(
            "Тип документа не выбран. Начните загрузку заново.",
            reply_markup=one(
                ("📄 Открыть документы", "documents_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if message.document:
        name = message.document.file_name or "document"
        mime = message.document.mime_type
        size = message.document.file_size
        file_id = message.document.file_id
    else:
        photo = message.photo[-1]
        name = f"photo_{photo.file_unique_id}.jpg"
        mime = "image/jpeg"
        size = photo.file_size
        file_id = photo.file_id

    document_service = DocumentService(db)
    storage = LocalStorageService()
    try:
        stored = await storage.save_telegram_file(
            bot=message.bot,
            telegram_file_id=file_id,
            case_id=case.id,
            original_name=name,
            mime_type=mime,
            file_size=size,
        )
    except UploadSecurityError as error:
        await document_service.record_rejected_upload(
            case_id=case.id,
            actor_id=user.id,
            document_type=document_type,
            file_name=safe_filename(name),
            reason_code=error.code,
            sha256=error.sha256,
        )
        await db.commit()
        logger.warning(
            "Document upload rejected: case=%s code=%s sha256=%s quarantined=%s",
            case.id,
            error.code,
            error.sha256,
            bool(error.quarantine_path),
        )
        await message.answer(
            f"⚠️ {error.user_message}\n\n"
            "Документ не сохранён. Исправьте файл и прикрепите его ещё раз.",
            reply_markup=one(
                ("Выбрать другой тип", "documents_upload_open"),
                ("Отменить действие", "nav_cancel"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception(
            "Document download or encryption failed: case=%s file_id=%s",
            case.id,
            file_id,
        )
        await message.answer(
            "⚠️ Не удалось безопасно обработать файл. Документ не сохранён.\n\n"
            "Прикрепите файл повторно либо выберите другой тип документа.",
            reply_markup=one(
                ("Выбрать другой тип", "documents_upload_open"),
                ("Отменить действие", "nav_cancel"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    try:
        document = await document_service.create_document(
            case=case,
            uploaded_by_user_id=user.id,
            document_type=document_type,
            file_name=stored.original_name,
            file_path=stored.storage_path,
            mime_type=stored.mime_type,
            file_size=stored.file_size,
            sha256=stored.sha256,
            detected_type=stored.detected_type,
            security_status=stored.security_status,
            scanned_at=stored.scanned_at,
            encryption_status=stored.encryption_status,
            encryption_key_id=stored.encryption_key_id,
            encryption_format_version=stored.encryption_format_version,
            encryption_envelope_id=stored.encryption_envelope_id,
            encrypted_data_key=stored.encrypted_data_key,
            encrypted_data_key_nonce=stored.encrypted_data_key_nonce,
            encrypted_at=stored.encrypted_at,
        )
        await db.commit()
    except DuplicateDocumentError as error:
        await db.rollback()
        try:
            storage.discard_stored_file(stored.storage_path)
        except Exception:
            logger.exception(
                "Duplicate document ciphertext cleanup failed: case=%s path=%s",
                case.id,
                stored.storage_path,
            )
        await state.clear()
        await message.answer(
            f"ℹ️ {error}",
            reply_markup=one(
                ("📋 Актуальные документы", "documents_list_open"),
                ("Загрузить другой файл", "documents_upload_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        try:
            storage.discard_stored_file(stored.storage_path)
        except Exception:
            logger.exception(
                "Unregistered document ciphertext cleanup failed: case=%s path=%s",
                case.id,
                stored.storage_path,
            )
        logger.exception(
            "Verified encrypted document could not be registered: case=%s sha256=%s",
            case.id,
            stored.sha256,
        )
        await message.answer(
            "⚠️ Файл обработан, но не зарегистрирован в деле. Документ не сохранён.\n\n"
            "Прикрепите его повторно либо вернитесь в раздел документов.",
            reply_markup=one(
                ("📄 Раздел документов", "documents_open"),
                ("Отменить действие", "nav_cancel"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await state.clear()
    await message.answer(
        f"✅ Файл безопасно загружен: {document.title}, версия {document.version}.\n\n"
        "Он ещё не передан юристу. Передайте новые файлы, когда закончите загрузку.",
        reply_markup=one(
            ("✅ Передать новые файлы юристу", "doc_finish_upload"),
            ("➕ Добавить ещё", "documents_upload_open"),
            ("📄 Обзор документов", "documents_open"),
            ("✉️ Задать вопрос по делу", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


async def _render_current_documents(callback: CallbackQuery, db, page: int = 0):
    case, documents = await _load_case_documents(callback, db)
    if not case:
        return
    active = _active_documents(documents)
    archived = _archived_documents(documents)
    if not active:
        next_step, buttons = _recommended_step(case, active)
        if archived:
            buttons.append((f"🕘 История версий ({len(archived)})", "documents_history_open"))
        buttons.extend(
            [
                ("⬅️ К обзору", "documents_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ]
        )
        await _safe_edit(
            callback,
            "📋 Актуальные документы\n\nПока документов нет.\n\n"
            f"Следующий шаг: {next_step}",
            reply_markup=one(*buttons),
        )
        return

    page_items, current_page, total_pages = _paginate(active, page)
    next_step, action_buttons = _recommended_step(case, active)
    buttons = _page_navigation("documents_current_page", current_page, total_pages)
    buttons.extend(action_buttons)
    if archived:
        buttons.append((f"🕘 История версий ({len(archived)})", "documents_history_open"))
    buttons.extend(
        [
            ("⬅️ К обзору", "documents_open"),
            ("✉️ Задать вопрос по делу", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    await _safe_edit(
        callback,
        "📋 Актуальные документы\n\n"
        + "\n\n".join(_document_block(item) for item in page_items)
        + f"\n\nСтраница {current_page + 1} из {total_pages}.\n\n"
        + f"Следующий шаг: {next_step}",
        reply_markup=one(*buttons),
        unchanged_notice="Список актуальных документов не изменился.",
    )


@router.callback_query(lambda c: c.data == "documents_list_open")
async def list_docs(callback: CallbackQuery, db):
    await _render_current_documents(callback, db, 0)


@router.callback_query(lambda c: c.data.startswith("documents_current_page:"))
async def list_docs_page(callback: CallbackQuery, db):
    try:
        page = int(callback.data.rsplit(":", 1)[1])
    except (TypeError, ValueError):
        page = 0
    await _render_current_documents(callback, db, page)


async def _render_document_history(callback: CallbackQuery, db, page: int = 0):
    case, documents = await _load_case_documents(callback, db)
    if not case:
        return
    archived = _archived_documents(documents)
    if not archived:
        await _safe_edit(
            callback,
            "🕘 История версий\n\nПредыдущих версий пока нет.",
            reply_markup=one(
                ("⬅️ К обзору документов", "documents_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
            unchanged_notice="История версий пока пуста.",
        )
        return

    page_items, current_page, total_pages = _paginate(archived, page)
    buttons = _page_navigation("documents_history_page", current_page, total_pages)
    buttons.extend(
        [
            ("📋 Актуальные документы", "documents_list_open"),
            ("⬅️ К обзору документов", "documents_open"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    await _safe_edit(
        callback,
        "🕘 История версий\n\n"
        "Эти файлы сохранены в истории, но больше не участвуют в текущей проверке.\n\n"
        + "\n\n".join(
            _document_block(item, history=True) for item in page_items
        )
        + f"\n\nСтраница {current_page + 1} из {total_pages}.",
        reply_markup=one(*buttons),
        unchanged_notice="Эта страница истории не изменилась.",
    )


@router.callback_query(lambda c: c.data == "documents_history_open")
async def documents_history(callback: CallbackQuery, db):
    await _render_document_history(callback, db, 0)


@router.callback_query(lambda c: c.data.startswith("documents_history_page:"))
async def documents_history_page(callback: CallbackQuery, db):
    try:
        page = int(callback.data.rsplit(":", 1)[1])
    except (TypeError, ValueError):
        page = 0
    await _render_document_history(callback, db, page)


@router.callback_query(lambda c: c.data == "doc_finish_upload")
async def finish(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text(
            "Активное дело не найдено. Документы не переданы.",
            reply_markup=one(*_new_case_buttons()),
        )
        return

    document_service = DocumentService(db)
    existing = await document_service.list_case_documents(case.id)
    active = _active_documents(existing)
    new_uploads = [item for item in active if _status(item) == "UPLOADED"]
    if not new_uploads:
        replacement = [
            item for item in active if _status(item) in _DOCUMENT_REPLACEMENT_STATUSES
        ]
        review = [item for item in active if _status(item) in _DOCUMENT_REVIEW_STATUSES]
        if replacement:
            text = (
                "Новых файлов для передачи нет. Юрист запросил новую версию:\n\n"
                + "\n\n".join(_document_block(item) for item in replacement[:4])
            )
            primary = ("🔁 Загрузить новую версию", "documents_upload_open")
        elif review:
            text = (
                "ℹ️ Все новые файлы уже переданы юристу. "
                "Обновите статусы или задайте вопрос по делу."
            )
            primary = ("🔄 Обновить статусы", "documents_open")
        else:
            text = "Новых файлов для передачи нет. Сначала добавьте документ."
            primary = ("➕ Добавить документ", "documents_upload_open")
        buttons = [
            primary,
            ("📋 Актуальные документы", "documents_list_open"),
            ("✉️ Задать вопрос по делу", "message_create"),
        ]
        if not active and case.route == "M2" and _case_status(case) in _M2_CAN_SKIP_STATUSES:
            buttons.insert(0, ("Продолжить без документов", "doc_skip_m2"))
        buttons.extend(
            [
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ]
        )
        await callback.message.edit_text(text, reply_markup=one(*buttons))
        return

    required_types = {"DDU"} if case.route == "M1" else set()
    try:
        await callback.answer("Передаём документы юристу…")
    except Exception:
        logger.warning("Document review submission callback acknowledgement failed")
    try:
        new_count = await document_service.send_documents_to_review(
            case=case,
            actor_id=user.id,
            required_types=required_types,
        )
        status = _case_status(case)
        if case.route == "M1" and status in _M1_COLLECTION_STATUSES:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M1_LAWYER_REVIEW,
                actor_type="client",
                actor_id=user.id,
                comment="Безопасные документы переданы юристу",
            )
            response_text = (
                "✅ Документы получены и переданы юристу на проверку.\n\n"
                f"Передано файлов: {new_count}.\n"
                "Мы сообщим, когда появится следующий шаг."
            )
        elif case.route == "M1":
            response_text = (
                "✅ Дополнительные документы переданы юристу на проверку.\n\n"
                f"Передано файлов: {new_count}. Текущий этап дела не изменён."
            )
        elif status in {
            CaseStatus.M2_DESCRIPTION_PENDING,
            CaseStatus.M2_DOCUMENTS_OPTIONAL,
        }:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M2_SLOT_PENDING,
                actor_type="client",
                actor_id=user.id,
                comment="Документы консультации сохранены",
            )
            response_text = (
                "✅ Документы сохранены и переданы команде.\n\n"
                f"Передано файлов: {new_count}. Теперь выберите время консультации."
            )
        elif status == CaseStatus.M2_CONSULTATION_BOOKED:
            response_text = (
                "✅ Документы добавлены к подтверждённой консультации.\n\n"
                f"Передано файлов: {new_count}. Дата и время сохранены."
            )
        else:
            response_text = (
                "✅ Документы переданы юридической команде.\n\n"
                f"Передано файлов: {new_count}. Текущий этап обращения сохранён."
            )
        await db.commit()
    except DocumentsAlreadySubmittedError:
        await db.rollback()
        await callback.message.edit_text(
            "ℹ️ Новые файлы уже переданы юристу. Повторная запись не создана.\n\n"
            "Обновите статусы документов или задайте вопрос по делу.",
            reply_markup=one(
                ("🔄 Обновить статусы", "documents_open"),
                ("📋 Актуальные документы", "documents_list_open"),
                ("✉️ Задать вопрос по делу", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except (DocumentSecurityPendingError, MissingRequiredDocumentsError) as error:
        await db.rollback()
        await callback.message.edit_text(
            f"⚠️ {error}",
            reply_markup=one(
                ("➕ Загрузить документ", "documents_upload_open"),
                ("📋 Актуальные документы", "documents_list_open"),
                ("✉️ Задать вопрос по делу", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except ValueError as error:
        await db.rollback()
        await callback.message.edit_text(
            f"Документы не переданы: {error}",
            reply_markup=one(
                ("🔄 Повторить передачу", "doc_finish_upload"),
                ("📋 Актуальные документы", "documents_list_open"),
                ("✉️ Задать вопрос по делу", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Document review submission failed: case=%s", case.id)
        await callback.message.edit_text(
            "Документы временно не переданы. Загруженные файлы сохранены.",
            reply_markup=one(
                ("🔄 Повторить передачу", "doc_finish_upload"),
                ("📋 Актуальные документы", "documents_list_open"),
                ("✉️ Задать вопрос по делу", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _present_committed_result(
        callback,
        response_text,
        reply_markup=one(*_after_documents_buttons(case)),
        saved_notice="Документы уже переданы юристу.",
    )


@router.callback_query(lambda c: c.data == "doc_skip_m2")
async def skip(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case or case.route != "M2":
        await callback.message.edit_text(
            "Пропуск документов для текущего обращения недоступен.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    status = _case_status(case)
    if status not in _M2_CAN_SKIP_STATUSES:
        await callback.message.edit_text(
            "На текущем этапе документы можно добавить, но пропуск уже не меняет статус.",
            reply_markup=one(*_after_documents_buttons(case)),
        )
        return
    try:
        if status != CaseStatus.M2_SLOT_PENDING:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M2_SLOT_PENDING,
                actor_type="client",
                actor_id=user.id,
                comment="Клиент продолжил консультацию без документов",
            )
        await db.commit()
    except ValueError as error:
        await db.rollback()
        await callback.message.edit_text(
            f"Переход не выполнен: {error}",
            reply_markup=one(
                ("🔄 Повторить", "doc_skip_m2"),
                ("📄 Документы", "documents_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await _present_committed_result(
        callback,
        "Хорошо. Документы можно добавить позже без потери выбранного этапа.",
        reply_markup=one(
            ("📅 Выбрать время", "consult_slot_open"),
            ("📄 Документы", "documents_open"),
            ("✉️ Задать вопрос по делу", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
        saved_notice="Переход уже сохранён. Документы можно добавить позже.",
    )
