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
_DOCUMENT_UPLOAD_STATES = {
    DocumentUploadStates.choosing_type.state,
    DocumentUploadStates.waiting_file.state,
}
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


async def _draft_target_case(db, ctx, *, user_id: int, state_data: dict):
    replacement_document_id = state_data.get("replacement_document_id")
    if replacement_document_id is not None:
        try:
            replacement = await db.get(Document, int(replacement_document_id))
        except (TypeError, ValueError):
            replacement = None
        if replacement is None:
            return None
        return await ctx.case_service.get_case_for_user(
            user_id=int(user_id),
            case_id=int(replacement.case_id),
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

    # Presentation must not keep a database transaction open while Telegram is
    # contacted. Snapshot every ORM value first, release the read transaction,
    # then update Redis/FSM and render the screen from scalars only.
    case_id = int(case.id)
    case_number = str(case.case_number)
    route = str(case.route or "").upper()
    status = _case_status(case)
    await db.rollback()

    await state.clear()
    await state.set_state(DocumentUploadStates.choosing_type)
    await state.update_data(document_case_id=case_id)

    items = [
        (f"Загрузить: {title}", _bound_type(case_id, code))
        for title, code in legacy_documents.TYPES
    ]
    if route == "M2" and status in _M2_CAN_SKIP_STATUSES:
        items.append(("Продолжить без документов", f"doc_skip_m2:v2:{case_id}"))
    items.append(("✖️ Отменить загрузку", "document_upload_discard_confirm"))

    requirement = (
        "Для передачи дела юридической команде обязательно загрузите актуальный ДДУ."
        if route == "M1"
        else "Для консультации документы необязательны, но помогут юристу подготовиться."
    )
    await callback.message.edit_text(
        "➕ ДОБАВИТЬ ДОКУМЕНТ\n"
        f"Обращение № {case_number}\n\n"
        "Выберите тип, затем прикрепите PDF, DOCX, JPG или PNG. Загрузка привязана именно к этому обращению. "
        "Пока она не завершена или явно не отменена, бот не даст случайно потерять этот контекст при навигации.\n\n"
        f"{requirement}",
        reply_markup=one(*items),
    )
    if notice:
        try:
            await callback.answer(notice)
        except Exception:
            pass


async def _show_stale_draft(callback: CallbackQuery, state: FSMContext, db, text: str) -> None:
    await state.clear()
    await db.rollback()
    await callback.message.edit_text(
        text,
        reply_markup=one(
            ("📄 Открыть документы", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


async def _resume_upload_draft(
    callback: CallbackQuery,
    state: FSMContext,
    db,
    *,
    requested_case_id: int | None = None,
) -> None:
    current_state = await state.get_state()
    state_data = await state.get_data()
    if current_state not in _DOCUMENT_UPLOAD_STATES:
        await _show_stale_draft(
            callback,
            state,
            db,
            "Эта попытка загрузки уже завершена или отменена. Откройте документы, если хотите добавить новый файл.",
        )
        return

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    target = await _draft_target_case(
        db,
        ctx,
        user_id=int(user.id),
        state_data=state_data,
    )
    if target is None or not client_document_upload_allowed(target):
        await _show_stale_draft(
            callback,
            state,
            db,
            "Обращение из этой загрузки уже завершено или его этап изменился. Черновик загрузки закрыт, другие дела не затронуты.",
        )
        return

    target_id = int(target.id)
    target_number = str(target.case_number)
    if requested_case_id is not None and int(requested_case_id) != target_id:
        await db.rollback()
        await callback.message.edit_text(
            "Эта старая кнопка относится не к тому черновику загрузки. Ничего не изменено; продолжите текущую загрузку из сохранённого контекста.",
            reply_markup=one(
                ("↩️ Продолжить текущую загрузку", "document_upload_resume_draft"),
                ("✖️ Отменить загрузку", "document_upload_discard_confirm"),
            ),
        )
        return

    if current_state == DocumentUploadStates.choosing_type.state:
        try:
            await ctx.case_service.select_case_for_user(
                user_id=int(user.id),
                case_id=target_id,
            )
            await db.commit()
        except Exception:
            await db.rollback()
            await callback.message.edit_text(
                "Не удалось безопасно вернуть обращение для загрузки. Черновик сохранён; повторите попытку позже или отмените его явно.",
                reply_markup=one(
                    ("↩️ Повторить", "document_upload_resume_draft"),
                    ("✖️ Отменить загрузку", "document_upload_discard_confirm"),
                ),
            )
            return
        await _render_bound_chooser(
            callback,
            state,
            db,
            notice=f"Возвращено обращение № {target_number}.",
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
            and int(replacement.case_id) == target_id
            and int(replacement.id) == int(replacement_document_id)
        )
    else:
        try:
            bound_case_id = int(state_data.get("document_case_id"))
        except (TypeError, ValueError):
            bound_case_id = 0
        binding_valid = bound_case_id == target_id

    document_type = str(state_data.get("document_type") or "").strip()
    if not binding_valid or document_type not in _DOCUMENT_TYPE_CODES:
        await _show_stale_draft(
            callback,
            state,
            db,
            "Не удалось подтвердить исходное обращение или тип документа. Файл не привязан ни к одному делу; начните загрузку заново.",
        )
        return

    document_label = _DOCUMENT_TYPE_LABELS.get(document_type, document_type)
    try:
        await ctx.case_service.select_case_for_user(
            user_id=int(user.id),
            case_id=target_id,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        await callback.message.edit_text(
            "Не удалось безопасно вернуть контекст загрузки. Черновик сохранён; повторите попытку или отмените загрузку.",
            reply_markup=one(
                ("↩️ Повторить", "document_upload_resume_draft"),
                ("✖️ Отменить загрузку", "document_upload_discard_confirm"),
            ),
        )
        return

    await callback.message.edit_text(
        "📎 ЗАГРУЗКА ВОССТАНОВЛЕНА\n"
        f"Обращение № {target_number}\n"
        f"Тип: {document_label}\n\n"
        "Контекст и выбранный тип сохранены. Отправьте файл ещё раз — перед сохранением он будет проверен и попадёт только в это обращение.",
        reply_markup=one(
            ("Выбрать другой тип", "documents_upload_open"),
            ("✖️ Отменить загрузку", "document_upload_discard_confirm"),
        ),
    )


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

    case_id = int(case.id)
    case_number = str(case.case_number)
    await db.rollback()
    await state.update_data(
        document_case_id=case_id,
        document_type=document_type,
    )
    await state.set_state(DocumentUploadStates.waiting_file)
    await callback.message.edit_text(
        "📎 ФАЙЛ ДЛЯ ДОКУМЕНТА\n"
        f"Обращение № {case_number}\n"
        f"Тип: {_DOCUMENT_TYPE_LABELS.get(document_type, document_type)}\n\n"
        "Прикрепите PDF, DOCX, JPG или PNG. Перед сохранением бот ещё раз проверит обращение и допустимый этап. "
        "Навигация не удалит этот выбор без отдельного подтверждения.",
        reply_markup=one(
            ("Выбрать другой тип", "documents_upload_open"),
            ("✖️ Отменить загрузку", "document_upload_discard_confirm"),
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
    """Restore an exact server-verified Case-bound upload draft."""

    requested_case_id = _parse_resume_case_id(callback.data)
    if requested_case_id is None:
        await callback.message.edit_text(
            "Кнопка восстановления загрузки повреждена. Черновик не изменён.",
            reply_markup=one(
                ("↩️ Продолжить текущую загрузку", "document_upload_resume_draft"),
                ("✖️ Отменить загрузку", "document_upload_discard_confirm"),
            ),
        )
        return
    await _resume_upload_draft(
        callback,
        state,
        db,
        requested_case_id=requested_case_id,
    )


@router.callback_query(lambda c: c.data == "document_upload_resume_draft")
async def resume_current_document_upload_draft(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    """Resume only the Case already proven by the server-side FSM draft."""

    await _resume_upload_draft(callback, state, db)


@router.callback_query(lambda c: c.data == "document_upload_discard_confirm")
async def confirm_document_upload_discard(
    callback: CallbackQuery,
    state: FSMContext,
):
    current_state = await state.get_state()
    if current_state not in _DOCUMENT_UPLOAD_STATES:
        await callback.message.edit_text(
            "Незавершённой загрузки уже нет.",
            reply_markup=one(
                ("📄 Документы", "documents_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await callback.message.edit_text(
        "✖️ ОТМЕНИТЬ ЗАГРУЗКУ?\n\n"
        "Черновик пока сохранён. Отмена удалит только незавершённый выбор файла/типа; уже безопасно сохранённые документы, версии и статусы дела не изменятся.",
        reply_markup=one(
            ("↩️ Продолжить загрузку", "document_upload_resume_draft"),
            ("Да, отменить загрузку", "document_upload_discard"),
        ),
    )


@router.callback_query(lambda c: c.data == "document_upload_discard")
async def discard_document_upload(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    await state.clear()
    await db.rollback()
    await callback.message.edit_text(
        "✅ Незавершённая загрузка отменена.\n\n"
        "Ранее сохранённые документы и данные обращения не изменены.",
        reply_markup=one(
            ("📄 Открыть документы", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


__all__ = ["router"]
