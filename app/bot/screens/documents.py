import logging

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import DocumentUploadStates
from app.domain.documents.document_service import (
    DocumentSecurityPendingError,
    DocumentService,
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
_DOCUMENT_STATUS_LABELS = {
    "UPLOADED": "Безопасно загружен",
    "PENDING": "Проверяет юрист",
    "PENDING_REVIEW": "Проверяет юрист",
    "REVIEW_PENDING": "Проверяет юрист",
    "REVIEW_REQUIRED": "Проверяет юрист",
    "NEEDS_REVIEW": "Проверяет юрист",
    "ON_REVIEW": "Проверяет юрист",
    "APPROVED": "Принят юристом",
    "ACCEPTED": "Принят юристом",
    "VERIFIED": "Принят юристом",
    "REJECTED": "Нужно заменить файл",
    "NEEDS_REUPLOAD": "Нужно загрузить новую версию",
    "ARCHIVED": "В архиве",
}
_DOCUMENT_REPLACEMENT_STATUSES = {"REJECTED", "NEEDS_REUPLOAD"}


def _case_status(case) -> CaseStatus:
    if isinstance(case.status, CaseStatus):
        return case.status
    return CaseStatus(str(case.status))


def _client_document_status(document) -> str:
    return _DOCUMENT_STATUS_LABELS.get(
        str(document.status),
        "Безопасно загружен",
    )


def _client_document_comment(document) -> str | None:
    comment = str(document.lawyer_comment or "").strip()
    if not comment:
        return None
    if str(document.status) in _DOCUMENT_REPLACEMENT_STATUSES:
        return f"Что исправить: {comment}"
    return f"Комментарий юриста: {comment}"


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
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    if status == CaseStatus.M2_PAYMENT_PENDING:
        return (
            ("Продолжить подтверждение", "consult_pay"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    return (
        ("📁 Моё дело", "my_case_open"),
        ("🏠 Главная", "nav_home"),
    )


@router.callback_query(lambda c: c.data == "documents_open")
async def docs(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text(
            "📄 Документы можно добавить после создания обращения.\n\n"
            "Начните с расчёта неустойки или записи на консультацию.",
            reply_markup=one(*_new_case_buttons()),
        )
        return

    items = [(f"Загрузить: {title}", f"doc_type:{code}") for title, code in TYPES]
    items.extend(
        [
            ("📋 Список документов", "documents_list_open"),
            ("✅ Завершить загрузку", "doc_finish_upload"),
        ]
    )
    if case.route == "M2" and _case_status(case) in _M2_CAN_SKIP_STATUSES:
        items.append(("Продолжить без документов", "doc_skip_m2"))
    items.extend(
        [
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ]
    )

    requirement = (
        "Для передачи дела на проверку обязательно загрузите ДДУ."
        if case.route == "M1"
        else "Для консультации документы необязательны, но помогут юристу подготовиться."
    )
    await callback.message.edit_text(
        "📄 Документы\n\n"
        "Поддерживаются PDF, DOCX, JPG и PNG. Перед сохранением каждый файл "
        "проходит проверку и защищается.\n\n"
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
    await state.update_data(document_type=document_type)
    await state.set_state(DocumentUploadStates.waiting_file)
    await callback.message.edit_text(
        "Прикрепите PDF, DOCX, JPG или PNG.\n\n"
        "После проверки бот подтвердит, что файл безопасно загружен.",
        reply_markup=one(
            ("Выбрать другой тип", "documents_open"),
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
                ("Выбрать другой тип", "documents_open"),
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
                ("Выбрать другой тип", "documents_open"),
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
                ("Выбрать другой тип", "documents_open"),
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
                ("📋 Список документов", "documents_list_open"),
                ("Загрузить другой файл", "documents_open"),
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
        "Теперь можно добавить ещё документы или передать загруженные файлы юристу.",
        reply_markup=one(
            ("Загрузить ещё", "documents_open"),
            ("📋 Список документов", "documents_list_open"),
            ("✅ Передать юристу", "doc_finish_upload"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "documents_list_open")
async def list_docs(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text(
            "📋 Список документов\n\nАктивное обращение не найдено.",
            reply_markup=one(*_new_case_buttons()),
        )
        return

    documents = await DocumentService(db).list_case_documents(case.id)
    if not documents:
        buttons = [("Загрузить документ", "documents_open")]
        if case.route == "M2" and _case_status(case) in _M2_CAN_SKIP_STATUSES:
            buttons.append(("Продолжить без документов", "doc_skip_m2"))
        buttons.extend(
            [
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ]
        )
        await callback.message.edit_text(
            "📋 Список документов\n\nПока документов нет.",
            reply_markup=one(*buttons),
        )
        return

    rows = []
    for document in documents:
        lines = [
            f"#{document.id} {document.title}, версия {document.version}",
            f"Статус: {_client_document_status(document)}",
        ]
        comment = _client_document_comment(document)
        if comment:
            lines.append(comment)
        rows.append("\n".join(lines))

    await callback.message.edit_text(
        "📋 Список документов\n\n" + "\n\n".join(rows),
        reply_markup=one(
            ("Загрузить документ", "documents_open"),
            ("✅ Передать новые файлы юристу", "doc_finish_upload"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


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

    required_types = {"DDU"} if case.route == "M1" else set()
    try:
        new_count = await DocumentService(db).send_documents_to_review(
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
            response_text = "✅ Документы переданы юристу на проверку."
        elif case.route == "M1":
            response_text = (
                "✅ Дополнительные документы переданы команде. "
                "Текущий этап дела не изменён."
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
            response_text = "✅ Документы сохранены. Теперь выберите время."
        elif status == CaseStatus.M2_CONSULTATION_BOOKED:
            response_text = (
                "✅ Документы добавлены к подтверждённой консультации. "
                "Дата и время сохранены."
            )
        else:
            response_text = (
                "✅ Документы переданы команде. Текущий этап обращения сохранён."
            )
        if new_count == 0:
            response_text += " Новых версий для передачи не было."
        await db.commit()
    except (DocumentSecurityPendingError, MissingRequiredDocumentsError) as error:
        await db.rollback()
        await callback.message.edit_text(
            f"⚠️ {error}",
            reply_markup=one(
                ("Загрузить документ", "documents_open"),
                ("📋 Список документов", "documents_list_open"),
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
                ("📋 Список документов", "documents_list_open"),
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
                ("📋 Список документов", "documents_list_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await callback.message.edit_text(
        response_text,
        reply_markup=one(*_after_documents_buttons(case)),
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
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await callback.message.edit_text(
        "Хорошо. Документы можно добавить позже без потери выбранного этапа.",
        reply_markup=one(
            ("📅 Выбрать время", "consult_slot_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )
