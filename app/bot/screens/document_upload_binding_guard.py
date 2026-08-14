from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.document_replacement_protection import client_document_upload_allowed
from app.bot.keyboards import one
from app.bot.screens import document_action_center, documents as legacy_documents
from app.bot.states import DocumentUploadStates

router = Router()


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
    if case_id <= 0 or document_type not in legacy_documents._DOCUMENT_TYPE_CODES:
        return None
    return case_id, document_type


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
    status = legacy_documents._case_status(case)
    if case.route == "M2" and status in legacy_documents._M2_CAN_SKIP_STATUSES:
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
        if case.route == "M1"
        else "Для консультации документы необязательны, но помогут юристу подготовиться."
    )
    await callback.message.edit_text(
        "➕ Добавить документ\n\n"
        f"Дело № {case.case_number}\n\n"
        "Выберите тип, затем прикрепите PDF, DOCX, JPG или PNG. Выбор привязан именно к этому делу; "
        "если активное обращение изменится до отправки файла, загрузка будет отменена.\n\n"
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
    # This raw callback is navigation only. The freshly rendered type buttons
    # carry the current case id and become the first provenance-bearing action.
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
        # Every legacy/unbound type selector is treated as stale provenance. It
        # may refresh the current chooser, but never arms waiting_file directly.
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
        "📎 Прикрепите PDF, DOCX, JPG или PNG.\n\n"
        f"Файл будет принят только в дело № {case.case_number}. Перед сохранением бот ещё раз проверит, "
        "что это обращение остаётся активным и находится на допустимом этапе.",
        reply_markup=one(
            ("Выбрать другой тип", "documents_upload_open"),
            ("Отменить действие", "nav_cancel"),
            ("🏠 Главная", "nav_home"),
        ),
    )


__all__ = ["router"]
