from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import DocumentUploadStates
from app.domain.documents.document_service import DOC_TITLES, DocumentService
from app.domain.statuses.case_statuses import CaseStatus
from app.models.document import Document

logger = logging.getLogger(__name__)
router = Router()

_REQUIRED_STATUS = "REQUIRED"
_REPLACEMENT_STATUSES = {"REJECTED", "NEEDS_REUPLOAD"}
_REVIEW_STATUSES = {"ON_REVIEW"}
_APPROVED_STATUSES = {"APPROVED", "ACCEPTED", "VERIFIED"}
_ARCHIVED_STATUS = "ARCHIVED"
_KNOWN_ACTIVE_STATUSES = {
    _REQUIRED_STATUS,
    "UPLOADED",
    *_REPLACEMENT_STATUSES,
    *_REVIEW_STATUSES,
    *_APPROVED_STATUSES,
}
_M2_CAN_SKIP_STATUSES = {
    CaseStatus.M2_DESCRIPTION_PENDING,
    CaseStatus.M2_DOCUMENTS_OPTIONAL,
    CaseStatus.M2_SLOT_PENDING,
}
_STATUS_LABELS = {
    "REQUIRED": "требуется загрузить",
    "UPLOADED": "готов к передаче юристу",
    "ON_REVIEW": "проверяет юрист",
    "APPROVED": "принят юристом",
    "ACCEPTED": "принят юристом",
    "VERIFIED": "принят юристом",
    "REJECTED": "нужно заменить",
    "NEEDS_REUPLOAD": "нужна новая версия",
}


def _status(document: Document) -> str:
    return str(document.status)


def _case_status(case) -> CaseStatus | None:
    if isinstance(case.status, CaseStatus):
        return case.status
    try:
        return CaseStatus(str(case.status))
    except (TypeError, ValueError):
        return None


def _short(value: object, limit: int = 220) -> str:
    clean = " ".join(str(value or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(limit - 1, 1)].rstrip() + "…"


def _active(documents: list[Document]) -> list[Document]:
    return [item for item in documents if _status(item) != _ARCHIVED_STATUS]


def _counts(documents: list[Document]) -> dict[str, int]:
    return {
        "required": sum(_status(item) == _REQUIRED_STATUS for item in documents),
        "new": sum(_status(item) == "UPLOADED" for item in documents),
        "review": sum(_status(item) in _REVIEW_STATUSES for item in documents),
        "approved": sum(_status(item) in _APPROVED_STATUSES for item in documents),
        "replacement": sum(
            _status(item) in _REPLACEMENT_STATUSES for item in documents
        ),
    }


def _document_line(document: Document) -> str:
    status = _status(document)
    label = _STATUS_LABELS.get(status, "статус уточняется")
    if status == _REQUIRED_STATUS:
        line = f"• {document.title} — {label}"
    else:
        line = f"• {document.title}, версия {document.version} — {label}"
    if status in _REPLACEMENT_STATUSES and document.lawyer_comment:
        line += f"\n  Что исправить: {_short(document.lawyer_comment)}"
    return line


def _reupload_callback(document: Document) -> str:
    return f"document_reupload:{int(document.id)}:{int(document.version or 1)}"


async def _clear_document_upload_state(state: FSMContext) -> None:
    current = await state.get_state()
    if current in {
        DocumentUploadStates.choosing_type.state,
        DocumentUploadStates.waiting_file.state,
    }:
        await state.clear()


async def _safe_present(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
        return
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            try:
                await callback.answer("Экран уже актуален.")
            except Exception:
                logger.warning("Document action-center callback acknowledgement failed")
            return
        logger.warning("Document action-center edit failed: %s", error)
    except (TelegramNetworkError, TelegramServerError):
        logger.warning("Document action-center edit failed due to Telegram transport")
    except Exception:
        logger.exception("Document action-center edit failed")

    try:
        await callback.message.answer(text, reply_markup=reply_markup)
    except Exception:
        logger.exception("Document action-center fallback message failed")
        try:
            await callback.answer(
                "Не удалось обновить экран. Откройте «Документы» ещё раз.",
                show_alert=True,
            )
        except Exception:
            logger.warning("Document action-center final acknowledgement failed")


async def _active_case(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    return user, case


def _no_case_markup():
    return one(
        ("🧮 Рассчитать неустойку", "calc_start"),
        ("💬 Связаться с юристом", "contact_lawyer"),
        ("🏠 Главная", "nav_home"),
    )


def _next_action(case, documents: list[Document]):
    counts = _counts(documents)
    replacements = [
        item for item in documents if _status(item) in _REPLACEMENT_STATUSES
    ]
    if replacements:
        document = replacements[0]
        comment = _short(document.lawyer_comment or "Учтите замечание юриста.")
        return (
            f"Заменить «{document.title}», версия {document.version}.\n"
            f"Что исправить: {comment}",
            [
                (
                    f"🔁 Заменить «{document.title}»",
                    _reupload_callback(document),
                )
            ],
        )

    unknown = [
        item for item in documents if _status(item) not in _KNOWN_ACTIVE_STATUSES
    ]
    if unknown:
        document = unknown[0]
        return (
            f"Статус «{document.title}» изменился и пока не поддерживается ботом. Не загружайте дубликат: откройте дело, чтобы увидеть актуальный обязательный шаг.",
            [("📁 К актуальному шагу дела", "my_case_open")],
        )

    required = [item for item in documents if _status(item) == _REQUIRED_STATUS]
    if required:
        document = required[0]
        return (
            f"Загрузить обязательный документ «{document.title}».",
            [("➕ Загрузить документ", "documents_upload_open")],
        )

    if counts["new"]:
        suffix = "файл" if counts["new"] == 1 else "новых файла"
        return (
            f"Передать юристу {counts['new']} {suffix}. До передачи файлы не входят в очередь проверки.",
            [("✅ Передать новые файлы юристу", "doc_finish_upload")],
        )

    if not documents and case.route == "M2":
        status = _case_status(case)
        if status in _M2_CAN_SKIP_STATUSES:
            return (
                "Документы для консультации необязательны. Можно перейти к следующему шагу дела или добавить материал для подготовки юриста.",
                [("➡️ Продолжить без документов", "doc_skip_m2")],
            )
        return (
            "Документы для консультации можно добавить при необходимости. Основной шаг сейчас находится в разделе «Моё дело».",
            [("📁 К следующему шагу дела", "my_case_open")],
        )

    if not documents and case.route == "M1":
        return (
            "Загрузить ДДУ. Без него M1-дело нельзя передать юристу на проверку.",
            [("➕ Загрузить документ", "documents_upload_open")],
        )

    if not documents:
        return (
            "Документов пока нет. Вернитесь к делу: там показан актуальный обязательный шаг для текущего маршрута.",
            [("📁 К следующему шагу дела", "my_case_open")],
        )

    if counts["review"]:
        return (
            "Дождаться проверки юриста. Пока проверка идёт, дополнительных действий по этим файлам не требуется.",
            [("🔄 Проверить статус", "documents_open")],
        )

    if counts["approved"] == len(documents):
        return (
            "Все актуальные документы приняты. Вернитесь к делу — там показан следующий обязательный шаг.",
            [("📁 К следующему шагу дела", "my_case_open")],
        )

    return (
        "Проверить комплект и добавить недостающий документ.",
        [("➕ Добавить документ", "documents_upload_open")],
    )


async def _render_home(callback: CallbackQuery, state: FSMContext, db) -> None:
    await _clear_document_upload_state(state)
    _, case = await _active_case(callback, db)
    if not case:
        await _safe_present(
            callback,
            "📄 ДОКУМЕНТЫ\n\n"
            "СЕЙЧАС\nАктивного дела пока нет.\n\n"
            "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
            "Создайте обращение через расчёт или свяжитесь с юридической командой.",
            reply_markup=_no_case_markup(),
        )
        return

    all_documents = await DocumentService(db).list_case_documents(case.id)
    documents = _active(all_documents)
    archived_count = len(all_documents) - len(documents)
    counts = _counts(documents)
    next_step, primary = _next_action(case, documents)

    summary = (
        f"Актуальных: {len(documents)} · требуется: {counts['required']} · "
        f"к передаче: {counts['new']} · на проверке: {counts['review']} · "
        f"принято: {counts['approved']} · требуют замены: {counts['replacement']}"
    )
    preview = "\n".join(_document_line(item) for item in documents[:5])
    if not preview:
        preview = "• Документов пока нет."
    elif len(documents) > 5:
        preview += f"\n• Ещё актуальных документов: {len(documents) - 5}."

    buttons = list(primary)
    primary_callbacks = {callback_data for _, callback_data in primary}
    if documents:
        buttons.append(("📋 Все актуальные документы", "documents_list_open"))
    if (
        "documents_upload_open" not in primary_callbacks
        and not counts["replacement"]
    ):
        buttons.append(("➕ Добавить документ", "documents_upload_open"))
    if archived_count:
        buttons.append(
            (f"🕘 История версий ({archived_count})", "documents_history_open")
        )
    buttons.extend(
        [
            ("✉️ Задать вопрос по документам", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ]
    )

    await _safe_present(
        callback,
        "📄 ДОКУМЕНТЫ\n"
        f"Обращение № {case.case_number}\n\n"
        "СЕЙЧАС\n"
        f"{summary}\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        f"{next_step}\n\n"
        "АКТУАЛЬНЫЕ ДОКУМЕНТЫ\n"
        f"{preview}",
        reply_markup=one(*buttons),
    )


@router.callback_query(lambda c: c.data == "documents_open")
async def documents_action_center(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    await _render_home(callback, state, db)


@router.callback_query(lambda c: c.data == "documents_list_open")
async def exact_replacement_document_list(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    await _clear_document_upload_state(state)
    _, case = await _active_case(callback, db)
    if not case:
        await _safe_present(
            callback,
            "Активное дело больше не найдено. Старая кнопка списка не создаёт новое обращение.",
            reply_markup=_no_case_markup(),
        )
        return

    all_documents = await DocumentService(db).list_case_documents(case.id)
    documents = _active(all_documents)
    replacements = [
        item for item in documents if _status(item) in _REPLACEMENT_STATUSES
    ]
    if not replacements:
        from app.bot.screens.documents import _render_current_documents

        await _render_current_documents(callback, db, 0)
        return

    preview = "\n\n".join(_document_line(item) for item in documents[:8])
    if len(documents) > 8:
        preview += f"\n\n• Ещё актуальных документов: {len(documents) - 8}."
    buttons = [
        (
            f"🔁 Заменить «{item.title}» · v{item.version}",
            _reupload_callback(item),
        )
        for item in replacements[:6]
    ]
    buttons.extend(
        [
            ("🕘 История версий", "documents_history_open"),
            ("✉️ Вопрос по документам", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    await _safe_present(
        callback,
        "📋 АКТУАЛЬНЫЕ ДОКУМЕНТЫ\n"
        f"Обращение № {case.case_number}\n\n"
        "СЕЙЧАС\nЮрист запросил исправление одного или нескольких файлов.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Выберите конкретный документ ниже. Каждая кнопка привязана к точному document_id и версии; устаревший запрос будет заблокирован перед загрузкой файла.\n\n"
        f"{preview}",
        reply_markup=one(*buttons),
    )


async def _stale_reupload(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    reason: str,
) -> None:
    await _clear_document_upload_state(state)
    await _safe_present(
        callback,
        "🔁 НОВАЯ ВЕРСИЯ ДОКУМЕНТА\n\n"
        "СЕЙЧАС\n"
        f"{reason}\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Откройте актуальные документы: бот покажет текущее решение юриста и допустимое действие.",
        reply_markup=one(
            ("📄 Открыть актуальные документы", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(
    lambda c: bool(c.data) and c.data.startswith("document_reupload:")
)
async def direct_document_reupload(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    parts = str(callback.data or "").split(":")
    if len(parts) != 3:
        await _stale_reupload(
            callback,
            state,
            reason="Ссылка на документ устарела или повреждена.",
        )
        return
    try:
        document_id = int(parts[1])
        expected_version = int(parts[2])
    except (TypeError, ValueError):
        await _stale_reupload(
            callback,
            state,
            reason="Ссылка на документ устарела или повреждена.",
        )
        return

    _, case = await _active_case(callback, db)
    if not case:
        await _clear_document_upload_state(state)
        await _safe_present(
            callback,
            "Активное дело больше не найдено. Новая версия не загружалась.",
            reply_markup=_no_case_markup(),
        )
        return

    document = await db.get(Document, document_id)
    if (
        document is None
        or int(document.case_id) != int(case.id)
        or int(document.version or 0) != expected_version
        or _status(document) not in _REPLACEMENT_STATUSES
    ):
        await _stale_reupload(
            callback,
            state,
            reason="Запрос юриста уже изменился: эта версия больше не требует замены.",
        )
        return

    latest = (
        await db.execute(
            select(Document)
            .where(Document.case_id == case.id)
            .where(Document.document_type == document.document_type)
            .where(Document.status != _ARCHIVED_STATUS)
            .order_by(Document.version.desc(), Document.id.desc())
            .limit(1)
        )
    ).scalars().first()
    if latest is None or int(latest.id) != int(document.id):
        await _stale_reupload(
            callback,
            state,
            reason="У этого документа уже появилась более новая актуальная версия.",
        )
        return

    if document.document_type not in DOC_TITLES:
        await _stale_reupload(
            callback,
            state,
            reason="Тип старого документа больше не поддерживает прямую замену.",
        )
        return

    await state.clear()
    await state.update_data(
        document_type=document.document_type,
        replacement_document_id=document.id,
        replacement_expected_version=expected_version,
    )
    await state.set_state(DocumentUploadStates.waiting_file)

    comment = _short(document.lawyer_comment or "Загрузите исправленную версию файла.")
    await _safe_present(
        callback,
        "🔁 НОВАЯ ВЕРСИЯ ДОКУМЕНТА\n"
        f"Обращение № {case.case_number}\n\n"
        "СЕЙЧАС\n"
        f"Юрист попросил заменить «{document.title}», версия {document.version}.\n"
        f"Что исправить: {comment}\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Прикрепите PDF, DOCX, JPG или PNG. Тип документа уже выбран — повторно выбирать его не нужно.\n\n"
        f"После безопасной проверки файл будет сохранён как следующая версия «{document.title}». Старый запрос останется в истории.",
        reply_markup=one(
            ("✖️ Отменить замену", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )
