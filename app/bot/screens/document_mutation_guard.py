from __future__ import annotations

import logging

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.document_replacement_protection import client_document_upload_allowed
from app.bot.keyboards import one
from app.bot.screens import document_action_center
from app.domain.documents.document_service import (
    DocumentSecurityPendingError,
    DocumentService,
    DocumentsAlreadySubmittedError,
    MissingRequiredDocumentsError,
)
from app.domain.statuses.case_statuses import CaseStatus
from app.models.case import Case

router = Router()
logger = logging.getLogger(__name__)

_RAW_MUTATIONS = {"doc_finish_upload", "doc_skip_m2"}
_M1_COLLECTION = {
    CaseStatus.M1_DOCUMENTS_PENDING,
    CaseStatus.M1_DOCUMENTS_RECEIVED,
    CaseStatus.M1_DOCS_REQUESTED,
}
_M2_SKIP = {
    CaseStatus.M2_DESCRIPTION_PENDING,
    CaseStatus.M2_DOCUMENTS_OPTIONAL,
    CaseStatus.M2_SLOT_PENDING,
}


def _status(case: Case) -> CaseStatus:
    return case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))


def _case_id(callback: CallbackQuery, action: str) -> int | None:
    value = str(callback.data or "")
    prefix = f"{action}:v2:"
    if not value.startswith(prefix):
        return None
    try:
        case_id = int(value[len(prefix) :])
    except ValueError:
        return None
    return case_id if case_id > 0 else None


async def _locked_owned_active_case(callback: CallbackQuery, db, case_id: int):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = (
        await db.execute(
            select(Case)
            .where(Case.id == int(case_id), Case.client_id == int(user.id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if case is None:
        raise LookupError("Дело из этого сообщения не найдено или недоступно")
    active = await ctx.case_service.get_active_case_for_user(user.id)
    if active is None or int(active.id) != int(case.id):
        raise ValueError("Это уже не текущее активное дело")
    if not client_document_upload_allowed(case):
        raise ValueError("Текущий этап больше не допускает передачу документов клиентом")
    return ctx, user, case


async def _refresh_documents(callback: CallbackQuery, state: FSMContext, db, notice: str) -> None:
    await db.rollback()
    try:
        await callback.answer(notice)
    except Exception:
        pass
    await document_action_center._render_home(callback, state, db)


@router.callback_query(lambda c: c.data in _RAW_MUTATIONS)
async def legacy_unbound_document_mutation_refresh(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    # Old Telegram keyboards have no case id. They may navigate back to the
    # current document center, but can no longer submit/skip documents for
    # whichever case happens to be active now.
    await _refresh_documents(
        callback,
        state,
        db,
        "Старая кнопка обновлена для текущего дела — ничего не изменено.",
    )


@router.callback_query(
    lambda c: bool(c.data) and c.data.startswith("doc_finish_upload:v2:")
)
async def finish_documents_for_exact_case(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    case_id = _case_id(callback, "doc_finish_upload")
    if case_id is None:
        await _refresh_documents(callback, state, db, "Некорректная кнопка — ничего не изменено.")
        return

    try:
        ctx, user, case = await _locked_owned_active_case(callback, db, case_id)
        service = DocumentService(db)
        existing = await service.list_case_documents(case.id)
        active_docs = [item for item in existing if str(item.status) != "ARCHIVED"]
        new_uploads = [item for item in active_docs if str(item.status) == "UPLOADED"]
        if not new_uploads:
            await _refresh_documents(
                callback,
                state,
                db,
                "Новых файлов для передачи нет — показываем актуальные документы.",
            )
            return

        required_types = {"DDU"} if str(case.route) == "M1" else set()
        submitted = await service.send_documents_to_review(
            case=case,
            actor_id=user.id,
            required_types=required_types,
        )
        status = _status(case)
        if str(case.route) == "M1":
            if status not in _M1_COLLECTION:
                raise ValueError("Этап M1 уже изменился; передача не выполняется")
            if status != CaseStatus.M1_DOCUMENTS_RECEIVED:
                await ctx.case_service.change_status(
                    case=case,
                    next_status=CaseStatus.M1_DOCUMENTS_RECEIVED,
                    actor_type="client",
                    actor_id=user.id,
                    comment=(
                        "Клиент передал безопасные документы юридической команде; "
                        "фактическая проверка юристом ещё не начата"
                    ),
                )
            result_text = (
                "✅ Документы переданы юридической команде.\n\n"
                f"Передано файлов: {submitted}. Сейчас ждём назначения ответственного и фактического начала проверки. "
                "Повторно отправлять эти файлы не нужно."
            )
            buttons = [
                ("🔄 Проверить статус", "documents_open"),
                ("✉️ Задать вопрос по делу", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ]
        elif status in {CaseStatus.M2_DESCRIPTION_PENDING, CaseStatus.M2_DOCUMENTS_OPTIONAL}:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M2_SLOT_PENDING,
                actor_type="client",
                actor_id=user.id,
                comment="Документы консультации сохранены и переданы команде",
            )
            result_text = (
                "✅ Документы сохранены для консультации.\n\n"
                f"Передано файлов: {submitted}. Теперь выберите свободное время."
            )
            buttons = [
                ("📅 Выбрать время", "consult_slot_open"),
                ("📄 Документы", "documents_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ]
        else:
            # M2 slot/payment/booked stages may accept additional evidence
            # without changing the already selected scheduling/payment stage.
            result_text = (
                "✅ Дополнительные документы сохранены для консультации.\n\n"
                f"Передано файлов: {submitted}. Текущий этап и выбранное время не изменены."
            )
            buttons = [
                ("📄 Документы", "documents_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ]
        await db.commit()
    except DocumentsAlreadySubmittedError:
        await _refresh_documents(
            callback,
            state,
            db,
            "Эти файлы уже переданы — повторная запись не создана.",
        )
        return
    except (DocumentSecurityPendingError, MissingRequiredDocumentsError, LookupError, ValueError) as error:
        await db.rollback()
        await callback.message.edit_text(
            f"Документы не переданы: {error}\n\nОткройте актуальный раздел документов и исправьте причину.",
            reply_markup=one(
                ("📄 Актуальные документы", "documents_open"),
                ("✉️ Написать команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Bound client document submission failed: case=%s", case_id)
        await callback.message.edit_text(
            "Документы временно не переданы. Загруженные файлы сохранены, статус дела не изменён.",
            reply_markup=one(
                ("📄 Актуальные документы", "documents_open"),
                ("✉️ Написать команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await callback.message.edit_text(result_text, reply_markup=one(*buttons))


@router.callback_query(
    lambda c: bool(c.data) and c.data.startswith("doc_skip_m2:v2:")
)
async def skip_documents_for_exact_m2_case(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    case_id = _case_id(callback, "doc_skip_m2")
    if case_id is None:
        await _refresh_documents(callback, state, db, "Некорректная кнопка — ничего не изменено.")
        return

    try:
        ctx, user, case = await _locked_owned_active_case(callback, db, case_id)
        status = _status(case)
        if str(case.route) != "M2" or status not in _M2_SKIP:
            raise ValueError("Пропуск документов больше не соответствует текущему этапу")
        if status != CaseStatus.M2_SLOT_PENDING:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M2_SLOT_PENDING,
                actor_type="client",
                actor_id=user.id,
                comment="Клиент продолжил консультацию без документов",
            )
        await db.commit()
    except (LookupError, ValueError) as error:
        await db.rollback()
        await callback.message.edit_text(
            f"Переход без документов не выполнен: {error}",
            reply_markup=one(
                ("📄 Документы", "documents_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Bound M2 document skip failed: case=%s", case_id)
        await callback.message.edit_text(
            "Переход временно не выполнен. Текущий этап не изменён.",
            reply_markup=one(
                ("📄 Документы", "documents_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await callback.message.edit_text(
        "Хорошо. Документы сейчас не обязательны. Можно выбрать время консультации, а материалы добавить позже, если это ещё будет допустимо на этапе дела.",
        reply_markup=one(
            ("📅 Выбрать время", "consult_slot_open"),
            ("📄 Документы", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


__all__ = ["router"]
