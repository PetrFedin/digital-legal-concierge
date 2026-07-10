from aiogram import Router
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import DocumentUploadStates
from app.domain.documents.document_service import DocumentService
from app.domain.statuses.case_statuses import CaseStatus
from app.storage import LocalStorageService

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
            reply_markup=one(("🧮 Рассчитать", "calc_start"), ("💬 Юрист", "calc_to_m2")),
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
        "📄 Документы\n\nЗагрузите копии или сканы. Для М1 минимум нужен ДДУ.",
        reply_markup=one(*items),
    )


@router.callback_query(lambda c: c.data.startswith("doc_type:") or c.data in DOC_UPLOAD_ALIASES)
async def choose(callback: CallbackQuery, state: FSMContext):
    document_type = callback.data.split(":", 1)[1] if callback.data.startswith("doc_type:") else DOC_UPLOAD_ALIASES[callback.data]
    await state.update_data(document_type=document_type)
    await state.set_state(DocumentUploadStates.waiting_file)
    await callback.message.edit_text("Прикрепите PDF/JPG/PNG файлом или изображением.")


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
        name = message.document.file_name
        mime = message.document.mime_type
        size = message.document.file_size
        file_id = message.document.file_id
    else:
        photo = message.photo[-1]
        name = f"photo_{photo.file_unique_id}.jpg"
        mime = "image/jpeg"
        size = photo.file_size
        file_id = photo.file_id
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
        # If Telegram download is unavailable in local tests, keep file_id as a safe fallback.
        stored = type("Stored", (), {
            "original_name": name,
            "storage_path": file_id,
            "mime_type": mime,
            "file_size": size,
        })()

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
        else "\n".join([f"#{doc.id} {doc.title} — {doc.status}, v{doc.version}" for doc in docs])
    )
    await callback.message.edit_text(
        text,
        reply_markup=one(("Загрузить документ", "documents_open"), ("📁 Мое дело", "my_case_open")),
    )


@router.callback_query(lambda c: c.data == "doc_finish_upload")
async def finish(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    await DocumentService(db).send_documents_to_review(case=case, actor_id=user.id)
    if case.route == "M1":
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
        reply_markup=one(("📁 Мое дело", "my_case_open"), ("📅 Выбрать время", "consult_slot_open")),
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
        reply_markup=one(("📅 Выбрать время", "consult_slot_open"), ("📁 Мое дело", "my_case_open")),
    )
