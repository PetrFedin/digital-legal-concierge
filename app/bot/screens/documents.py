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


@router.callback_query(lambda c: c.data == "documents_open")
async def docs(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text(
            "📄 Документы доступны после создания обращения.",
            reply_markup=one(
                ("🧮 Рассчитать", "calc_start"),
                ("💬 Юрист", "calc_to_m2"),
            ),
        )
        return
    items = [(f"Загрузить {title}", f"doc_type:{code}") for title, code in TYPES]
    items += [
        ("📋 Список документов", "documents_list_open"),
        ("Я загрузил все документы", "doc_finish_upload"),
        ("📁 Мое дело", "my_case_open"),
    ]
    if case.route == "M2":
        items.insert(-1, ("Пропустить документы", "doc_skip_m2"))
    await callback.message.edit_text(
        "📄 Документы\n\n"
        "Загрузите копии или сканы в PDF, DOCX, JPG или PNG. "
        "Для М1 минимум нужен ДДУ.",
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
        "Прикрепите PDF, DOCX, JPG или PNG. "
        "Файл будет проверен перед сохранением."
    )


@router.message(DocumentUploadStates.waiting_file)
async def upload(message: Message, state: FSMContext, db):
    if not message.document and not message.photo:
        await message.answer("⚠️ Прикрепите файл или изображение.")
        return

    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await message.answer("Нет активного дела. Начните с расчета или консультации.")
        await state.clear()
        return

    data = await state.get_data()
    document_type = data["document_type"]
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
    try:
        stored = await LocalStorageService().save_telegram_file(
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
            "Документ не сохранён. Прикрепите исправленный файл."
        )
        return
    except Exception:
        await db.rollback()
        logger.exception(
            "Document download failed before verification: case=%s file_id=%s",
            case.id,
            file_id,
        )
        await message.answer(
            "⚠️ Не удалось безопасно скачать и проверить файл. "
            "Документ не сохранён. Повторите загрузку."
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
        )
        await db.commit()
    except DuplicateDocumentError as error:
        await db.rollback()
        await state.clear()
        await message.answer(
            f"ℹ️ {error}",
            reply_markup=one(
                ("📋 Список", "documents_list_open"),
                ("Загрузить другой", "documents_open"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception(
            "Verified document could not be registered: case=%s sha256=%s",
            case.id,
            stored.sha256,
        )
        await message.answer(
            "⚠️ Файл прошёл проверку, но его не удалось зарегистрировать. "
            "Повторите загрузку."
        )
        return

    await state.clear()
    await message.answer(
        f"✅ Документ проверен и загружен: {document.title}, версия {document.version}",
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
    documents = await DocumentService(db).list_case_documents(case.id) if case else []
    text = "📋 Список документов\n\n" + (
        "Пока пусто."
        if not documents
        else "\n".join(
            [
                f"#{document.id} {document.title} — {document.status}, "
                f"v{document.version}, проверка: {document.security_status}"
                for document in documents
            ]
        )
    )
    await callback.message.edit_text(
        text,
        reply_markup=one(
            ("Загрузить документ", "documents_open"),
            ("📁 Мое дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: c.data == "doc_finish_upload")
async def finish(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    try:
        await DocumentService(db).send_documents_to_review(
            case=case,
            actor_id=user.id,
        )
    except DocumentSecurityPendingError as error:
        await db.rollback()
        await callback.message.edit_text(
            f"⚠️ {error}",
            reply_markup=one(
                ("📋 Список документов", "documents_list_open"),
                ("Загрузить документ", "documents_open"),
            ),
        )
        return

    if case.route == "M1":
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.M1_LAWYER_REVIEW,
            actor_type="client",
            actor_id=user.id,
            force=True,
            comment="Проверенные документы переданы юристу",
        )
        response_text = "✅ Проверенные документы переданы на проверку юристу."
    else:
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.M2_SLOT_PENDING,
            actor_type="client",
            actor_id=user.id,
            force=True,
            comment="Проверенные документы М2 сохранены",
        )
        response_text = "✅ Документы сохранены. Теперь выберите время консультации."
    await db.commit()
    await callback.message.edit_text(
        response_text,
        reply_markup=one(
            ("📁 Мое дело", "my_case_open"),
            ("📅 Выбрать время", "consult_slot_open"),
        ),
    )


@router.callback_query(lambda c: c.data == "doc_skip_m2")
async def skip(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    await ctx.case_service.change_status(
        case=case,
        next_status=CaseStatus.M2_SLOT_PENDING,
        actor_type="client",
        actor_id=user.id,
        force=True,
        comment="Документы пропущены",
    )
    await db.commit()
    await callback.message.edit_text(
        "Хорошо, документы можно добавить позже.",
        reply_markup=one(
            ("📅 Выбрать время", "consult_slot_open"),
            ("📁 Мое дело", "my_case_open"),
        ),
    )
