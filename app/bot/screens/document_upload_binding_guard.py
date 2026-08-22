from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.document_replacement_protection import client_document_upload_allowed
from app.bot.keyboards import one
from app.bot.screens import document_action_center, documents as legacy_documents
from app.bot.states import DocumentUploadStates
from app.domain.statuses.case_statuses import CaseStatus
from app.models.document import Document

router = Router()

_DOCUMENT_TYPE_CODES = {code for _, code in legacy_documents.TYPES}
_DOCUMENT_TYPE_LABELS = {code: title for title, code in legacy_documents.TYPES}
_M2_CAN_SKIP_STATUSES = {
    CaseStatus.M2_DESCRIPTION_PENDING,
    CaseStatus.M2_DOCUMENTS_OPTIONAL,
    CaseStatus.M2_SLOT_PENDING,
}


def _case_status(case) -> CaseStatus | None:
    try:
        return case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))
    except (TypeError, ValueError):
        return None


def _bound_type(case_id: int, document_type: str) -> str:
    return f"doc_type:v2:{int(case_id)}:{document_type}"


def _parse_bound_type(value: str) -> tuple[int, str] | None:
    prefix = "doc_type:v2:"
    if not str(value or "").startswith(prefix):
        return None
    tail = str(value)[len(prefix) :]
    case_raw, separator, document_type = tail.partition(":")
    if not separator:
        return None
    try:
        case_id = int(case_raw)
    except ValueError:
        return None
    if case_id <= 0 or document_type not in _DOCUMENT_TYPE_CODES:
        return None
    return case_id, document_type


def _parse_resume_case_id(value: str | None) -> int | None:
    prefix = "document_upload_resume:v2:"
    raw = str(value or "")
    if not raw.startswith(prefix):
        return None
    tail = raw[len(prefix) :]
    if not tail or ":" in tail:
        return None
    try:
        case_id = int(tail)
    except ValueError:
        return None
    return case_id if case_id > 0 else None


async def _active_scope(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case is None or not client_document_upload_allowed(case):
        return ctx, user, None
    return ctx, user, case


async def _render_bound_chooser(
    callback: CallbackQuery,
    state: FSMContext,
    db,
    *,
    notice: str | None = None,
) -> None:
    _ctx, _user, case = await _active_scope(callback, db)
    if case is None:
        await state.clear()
        await db.rollback()
        await document_action_center._render_home(callback, state, db)
        return

    await state.clear()
    await state.set_state(DocumentUploadStates.choosing_type)
    await state.update_data(document_case_id=int(case.id))

    items = [
        (f"Загрузить: {title}", _bound_type(case.id, code))
        for title, code in legacy_documents.TYPES
    ]
    status = _case_status(case)
    if str(case.route or "").upper() == "M2" and status in _M2_CAN_SKIP_STATUSES:
        items.append(("Продолжить без документов", f"doc_skip_m2:v2:{int(case.id)}"))
    items.extend(
        [
            ("⬅️ К обзору документов", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    requirement = (
        "Для передачи дела юридической команде обязательно загрузите актуальный ДДУ."
        if str(case.route or "").upper() == "M1"
        else "Для консультации документы необязательны, но помогут юристу подготовиться."
    )
    await callback.message.edit_text(
        "➕ ДОБАВИТЬ ДОКУМЕНТ\n"
        f"Обращение № {case.case_number}\n\n"
        "Выберите тип, затем прикрепите PDF, DOCX, JPG или PNG. Загрузка привязана именно к этому обращению. "
        "Если вы переключитесь на другое дело до отправки файла, бот не перепутает контекст: выбор документа сохранится и предложит вернуться сюда.\n\n"
        f"{requirement}",
        reply_markup=one(*items),
    )
    if notice:
        try:
            await callback.answer(notice)
        except Exception:
            pass


@router.callback_query(lambda c: c.data == "documents_upload_open")
async def safe_generic_upload_entry(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    # Raw callback is navigation only. Freshly rendered type buttons carry the
    # exact current case id and become the first provenance-bearing action.
    await _render_bound_chooser(callback, state, db)


@router.callback_query(
    lambda c: bool(c.data)
    and (
        c.data.startswith("doc_type:")
        or c.data in legacy_documents.DOC_UPLOAD_ALIASES
    )
)
async def bound_document_type_choice(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    value = str(callback.data or "")
    parsed = _parse_bound_type(value)
    if parsed is None:
        # Every legacy/unbound type selector is stale provenance. It may refresh
        # the current chooser but never arms waiting_file directly.
        await _render_bound_chooser(
            callback,
            state,
            db,
            notice="Старая кнопка обновлена для текущего дела.",
        )
        return

    expected_case_id, document_type = parsed
    _ctx, _user, case = await _active_scope(callback, db)
    if case is None or int(case.id) != int(expected_case_id):
        await state.clear()
        await db.rollback()
        await callback.message.edit_text(
            "Эта кнопка относится к другому обращению. Режим загрузки не включён и файл не будет принят по старому экрану.",
            reply_markup=one(
                ("📄 Открыть актуальные документы", "documents_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await state.update_data(
        document_case_id=int(case.id),
        document_type=document_type,
    )
    await state.set_state(DocumentUploadStates.waiting_file)
    await callback.message.edit_text(
        "📎 ФАЙЛ ДЛЯ ДОКУМЕНТА\n"
        f"Обращение № {case.case_number}\n"
        f"Тип: {_DOCUMENT_TYPE_LABELS.get(document_type, document_type)}\n\n"
        "Прикрепите PDF, DOCX, JPG или PNG. Перед сохранением бот ещё раз проверит обращение и допустимый этап."
        " Если вы случайно откроете другое дело, этот выбор не потеряется.",
        reply_markup=one(
            ("Выбрать другой тип", "documents_upload_open"),
            ("Отменить загрузку", "nav_cancel"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(
    lambda c: bool(c.data) and c.data.startswith("document_upload_resume:v2:")
)
async def resume_document_upload_for_exact_case(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    """Restore the exact Case context without discarding an armed upload draft.

    A Telegram file message can arrive after the client switched to another
    active matter. The stage middleware deliberately does not download that
    file. This callback re-selects only the server-verified Case that owns the
    existing FSM draft, while preserving document type/replacement provenance.
    The client can then resend the same Telegram file without rebuilding the
    flow from the beginning.
    """

    requested_case_id = _parse_resume_case_id(callback.data)
    current_state = await state.get_state()
    state_data = await state.get_data()
    if (
        requested_case_id is None
        or current_state != DocumentUploadStates.waiting_file.state
    ):
        await state.clear()
        await db.rollback()
        await callback.message.edit_text(
            "Эта попытка загрузки уже неактуальна. Откройте документы и начните загрузку из текущего обращения.",
            reply_markup=one(
                ("📄 Открыть документы", "documents_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
    target = next(
        (item for item in active_cases if int(item.id) == int(requested_case_id)),
        None,
    )
    if target is None or not client_document_upload_allowed(target):
        await state.clear()
        await db.rollback()
        await callback.message.edit_text(
            "Обращение из этой загрузки уже завершено или его этап изменился. Черновик загрузки закрыт, чужое дело не затронуто.",
            reply_markup=one(
                ("📄 Актуальные документы", "documents_open"),
                ("📁 Мои обращения", "my_cases_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    replacement_document_id = state_data.get("replacement_document_id")
    if replacement_document_id is not None:
        try:
            replacement = await db.get(Document, int(replacement_document_id))
        except (TypeError, ValueError):
            replacement = None
        binding_valid = bool(
            replacement is not None
            and int(replacement.case_id) == int(target.id)
            and int(replacement.id) == int(replacement_document_id)
        )
    else:
        try:
            bound_case_id = int(state_data.get("document_case_id"))
        except (TypeError, ValueError):
            bound_case_id = 0
        binding_valid = bound_case_id == int(target.id)

    document_type = str(state_data.get("document_type") or "").strip()
    if not binding_valid or document_type not in _DOCUMENT_TYPE_CODES:
        await state.clear()
        await db.rollback()
        await callback.message.edit_text(
            "Не удалось подтвердить исходное обращение или тип документа. Файл не привязан ни к одному делу; начните загрузку заново.",
            reply_markup=one(
                ("📄 Открыть документы", "documents_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    try:
        await ctx.case_service.select_case_for_user(
            user_id=int(user.id),
            case_id=int(target.id),
        )
        case_number = str(target.case_number)
        document_label = _DOCUMENT_TYPE_LABELS.get(document_type, document_type)
        await db.commit()
    except Exception:
        await db.rollback()
        await callback.message.edit_text(
            "Не удалось безопасно вернуть контекст загрузки. Черновик сохранён; повторите попытку или отмените загрузку.",
            reply_markup=one(
                (f"↩️ Вернуться к обращению № {target.case_number}", f"document_upload_resume:v2:{int(target.id)}"),
                ("Отменить загрузку", "nav_cancel"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await callback.message.edit_text(
        "📎 ЗАГРУЗКА ВОССТАНОВЛЕНА\n"
        f"Обращение № {case_number}\n"
        f"Тип: {document_label}\n\n"
        "Контекст и выбранный тип сохранены. Теперь отправьте тот же файл ещё раз — он будет проверен и сохранён только в это обращение.",
        reply_markup=one(
            ("Выбрать другой тип", "documents_upload_open"),
            ("Отменить загрузку", "nav_cancel"),
            ("🏠 Главная", "nav_home"),
        ),
    )


__all__ = ["router"]
