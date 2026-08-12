from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.documents.document_service import DocumentService, document_is_usable
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.document_statuses import DocumentStatus
from app.models.case import Case
from app.models.document import Document
from app.security.document_access import DocumentActor
from app.storage import StoredFile

SERVICE_CONTRACT_TYPE = "SERVICE_CONTRACT"
SERVICE_CONTRACT_TITLE = "Договор на юридические услуги"
CLIENT_CONTRACT_CONFIRMED_ACTION = "CLIENT_SERVICE_CONTRACT_CONFIRMED"
CONTRACT_PUBLISHED_ACTION = "SERVICE_CONTRACT_PUBLISHED"


async def current_service_contract(
    db: AsyncSession,
    *,
    case_id: int,
) -> Document | None:
    rows = list(
        (
            await db.execute(
                select(Document)
                .where(Document.case_id == int(case_id))
                .where(Document.document_type == SERVICE_CONTRACT_TYPE)
                .where(Document.status != DocumentStatus.ARCHIVED)
                .order_by(Document.version.desc(), Document.id.desc())
            )
        ).scalars().all()
    )
    for document in rows:
        if document.status == DocumentStatus.APPROVED and document_is_usable(document):
            return document
    return None


def _actor_type(actor: DocumentActor) -> str:
    return "lawyer" if actor.role == "lawyer" else "admin_user"


def _actor_id(actor: DocumentActor) -> int:
    return int(actor.lawyer_id or actor.account_id)


async def publish_service_contract(
    db: AsyncSession,
    *,
    actor: DocumentActor,
    case: Case,
    stored: StoredFile,
) -> Document:
    if str(case.route or "") != "M1" or str(case.status) != CaseStatus.M1_CONTRACT_READY.value:
        raise ValueError(
            "Публиковать договор можно только на активном этапе подготовки договора M1"
        )

    document = await DocumentService(db).create_document(
        case=case,
        uploaded_by_user_id=None,
        document_type=SERVICE_CONTRACT_TYPE,
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
    document.title = SERVICE_CONTRACT_TITLE
    document.status = DocumentStatus.APPROVED
    document.lawyer_comment = "Опубликован юридической командой для подтверждения клиентом"

    previous = list(
        (
            await db.execute(
                select(Document)
                .where(Document.case_id == case.id)
                .where(Document.document_type == SERVICE_CONTRACT_TYPE)
                .where(Document.id != document.id)
                .where(Document.status != DocumentStatus.ARCHIVED)
                .with_for_update()
            )
        ).scalars().all()
    )
    archived = []
    for item in previous:
        archived.append(
            {
                "document_id": int(item.id),
                "version": int(item.version or 1),
                "status": str(item.status),
            }
        )
        item.status = DocumentStatus.ARCHIVED

    await add_case_history_event(
        db,
        actor_type=_actor_type(actor),
        actor_id=_actor_id(actor),
        case_id=case.id,
        action=CONTRACT_PUBLISHED_ACTION,
        old_value={"archived_contracts": archived} if archived else None,
        new_value={
            "document_id": int(document.id),
            "document_type": SERVICE_CONTRACT_TYPE,
            "version": int(document.version or 1),
            "sha256": document.sha256,
            "file_name": document.file_name,
            "security_status": document.security_status,
            "encryption_status": document.encryption_status,
        },
        comment=(
            "Опубликована конкретная защищённая версия договора. "
            "Предыдущие незакрытые версии выведены из клиентского действия."
        ),
    )
    await NotificationEngine(db).emit(
        event_code="M1_CONTRACT_PUBLISHED",
        case_id=case.id,
        user_id=case.client_id,
        payload={
            "case_number": case.case_number,
            "version": int(document.version or 1),
        },
        dedupe_key=f"case:{case.id}:contract:{document.id}:published",
    )
    await db.flush()
    return document


async def confirm_service_contract(
    db: AsyncSession,
    *,
    case: Case,
    client_id: int,
    document: Document,
):
    if str(case.route or "") != "M1" or str(case.status) != CaseStatus.M1_CONTRACT_READY.value:
        raise ValueError("Этап договора уже изменился. Обновите «Моё дело»")
    current = await current_service_contract(db, case_id=case.id)
    if current is None or int(current.id) != int(document.id):
        raise ValueError(
            "Версия договора изменилась после открытия. Откройте актуальный договор и подтвердите его заново"
        )

    await add_case_history_event(
        db,
        actor_type="client",
        actor_id=client_id,
        case_id=case.id,
        action=CLIENT_CONTRACT_CONFIRMED_ACTION,
        new_value={
            "document_id": int(document.id),
            "version": int(document.version or 1),
            "sha256": document.sha256,
            "file_name": document.file_name,
            "confirmation_channel": "telegram",
        },
        comment=(
            "Клиент подтвердил ознакомление и согласие с конкретной версией договора в Telegram. "
            "Событие не заявляется как квалифицированная электронная подпись."
        ),
    )
    await CaseService(db).change_status(
        case=case,
        next_status=CaseStatus.M1_WAITING_PAYMENT_30000,
        actor_type="client",
        actor_id=client_id,
        comment=(
            f"Клиент подтвердил договор document_id={document.id}, version={document.version}"
        ),
    )
    payment = await PaymentService(db).get_or_create_payment(
        case=case,
        payment_code=PaymentCode.M1_INITIAL_PAYMENT,
    )
    await db.flush()
    return payment


__all__ = [
    "CLIENT_CONTRACT_CONFIRMED_ACTION",
    "CONTRACT_PUBLISHED_ACTION",
    "SERVICE_CONTRACT_TITLE",
    "SERVICE_CONTRACT_TYPE",
    "confirm_service_contract",
    "current_service_contract",
    "publish_service_contract",
]
