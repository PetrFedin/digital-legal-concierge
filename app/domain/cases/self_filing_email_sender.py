from __future__ import annotations

import asyncio
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.self_filing_documents import (
    SELF_FILING_DELIVERABLE_FIELDS,
    SELF_FILING_DELIVERABLE_TYPES,
)
from app.domain.cases.self_filing_service import (
    EMAIL_FAILED,
    EMAIL_QUEUED,
    EMAIL_SENDING,
    EMAIL_SENT,
    SelfFilingService,
)
from app.domain.documents.document_service import document_is_usable
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.statuses.document_statuses import DocumentStatus
from app.models.case import Case
from app.models.document import Document
from app.models.self_filing_package import SelfFilingPackage
from app.storage import LocalStorageService


class SelfFilingEmailConfigurationError(RuntimeError):
    pass


def email_delivery_configuration_error() -> str | None:
    provider = str(settings.self_filing_email_provider or "").strip().lower()
    if provider != "smtp":
        return "SELF_FILING_EMAIL_PROVIDER должен быть smtp"

    host = str(settings.self_filing_smtp_host or "").strip()
    from_email = str(settings.self_filing_smtp_from_email or "").strip().lower()
    username = str(settings.self_filing_smtp_username or "").strip()
    password = str(settings.self_filing_smtp_password or "")
    try:
        port = int(settings.self_filing_smtp_port or 0)
    except (TypeError, ValueError):
        port = 0

    if not host:
        return "SELF_FILING_SMTP_HOST не задан"
    if not 1 <= port <= 65535:
        return "SELF_FILING_SMTP_PORT должен быть в диапазоне 1..65535"
    if not from_email or "@" not in from_email or from_email.startswith("@") or from_email.endswith("@"):
        return "SELF_FILING_SMTP_FROM_EMAIL задан некорректно"
    if bool(username) != bool(password):
        return (
            "SELF_FILING_SMTP_USERNAME и SELF_FILING_SMTP_PASSWORD должны быть "
            "заданы вместе либо оба отсутствовать"
        )
    if int(settings.self_filing_email_max_attempts or 0) < 1:
        return "SELF_FILING_EMAIL_MAX_ATTEMPTS должен быть больше 0"
    if int(settings.self_filing_email_timeout_seconds or 0) < 5:
        return "SELF_FILING_EMAIL_TIMEOUT_SECONDS должен быть не меньше 5"

    return None


def email_delivery_configured() -> bool:
    return email_delivery_configuration_error() is None


def require_email_delivery_configured() -> None:
    error = email_delivery_configuration_error()
    if error is not None:
        raise SelfFilingEmailConfigurationError(
            "Email-доставка пакета не готова: "
            + error
            + ". Нельзя открывать оплату 15 000 ₽ до исправления конфигурации "
            "и контрольной проверки доставки."
        )


def _stable_message_id(package: SelfFilingPackage) -> str:
    from_email = str(settings.self_filing_smtp_from_email or "").strip()
    domain = from_email.rsplit("@", 1)[1] if "@" in from_email else "legal-concierge.local"
    return f"<self-filing-{int(package.id)}-v{int(package.version or 1)}@{domain}>"


def _smtp_send(message: EmailMessage) -> None:
    timeout = max(5, int(settings.self_filing_email_timeout_seconds or 30))
    with smtplib.SMTP(
        str(settings.self_filing_smtp_host),
        int(settings.self_filing_smtp_port),
        timeout=timeout,
    ) as smtp:
        smtp.ehlo()
        if bool(settings.self_filing_smtp_starttls):
            smtp.starttls()
            smtp.ehlo()
        username = str(settings.self_filing_smtp_username or "")
        password = str(settings.self_filing_smtp_password or "")
        if username or password:
            if not username or not password:
                raise SelfFilingEmailConfigurationError(
                    "SMTP username/password должны быть заданы вместе"
                )
            smtp.login(username, password)
        smtp.send_message(message)


async def send_self_filing_email_verification(
    *,
    to_email: str,
    code: str,
    case_number: str,
    package_id: int,
    challenge_version: int,
) -> str:
    """Send a one-time mailbox ownership code without persisting plaintext."""

    require_email_delivery_configured()
    clean_email = str(to_email or "").strip().lower()
    clean_code = str(code or "").strip()
    if not clean_email or "@" not in clean_email:
        raise ValueError("Email для проверки не задан")
    if len(clean_code) != 6 or not clean_code.isdigit():
        raise ValueError("Некорректный одноразовый код")

    from_email = str(settings.self_filing_smtp_from_email or "").strip()
    domain = (
        from_email.rsplit("@", 1)[1]
        if "@" in from_email
        else "legal-concierge.local"
    )
    message_id = (
        f"<self-filing-verify-{int(package_id)}-"
        f"v{int(challenge_version)}@{domain}>"
    )
    message = EmailMessage(policy=SMTP)
    message["Subject"] = "Код подтверждения email — Legal Concierge"
    message["From"] = from_email
    message["To"] = clean_email
    message["Message-ID"] = message_id
    message.set_content(
        "Подтвердите email для получения юридических документов.\n\n"
        f"Обращение: {case_number}\n"
        f"Код подтверждения: {clean_code}\n\n"
        "Код действует ограниченное время. Если вы не запрашивали подтверждение, "
        "не сообщайте код третьим лицам и не отвечайте на это письмо."
    )
    await asyncio.to_thread(_smtp_send, message)
    return message_id


class SelfFilingEmailSender:
    """Durable outbox-like SMTP delivery for approved self-filing packages.

    SMTP itself cannot provide transactional exactly-once semantics. We therefore
    persist a stable Message-ID and every attempt, never delete the source
    document, and make the business close idempotent after a confirmed send.
    """

    def __init__(self, db):
        self.db = db
        self.notifications = NotificationEngine(db)

    async def _locked_package(self, package_id: int) -> SelfFilingPackage | None:
        return (
            await self.db.execute(
                select(SelfFilingPackage)
                .where(SelfFilingPackage.id == int(package_id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def _compose(
        self,
        *,
        package: SelfFilingPackage,
        case: Case,
        documents: list[Document],
    ) -> EmailMessage:
        if not package.delivery_email or not package.email_confirmed_at:
            raise ValueError("Email клиента не подтверждён")

        by_type = {str(item.document_type): item for item in documents}
        if set(by_type) != set(SELF_FILING_DELIVERABLE_TYPES):
            raise ValueError(
                "Email можно отправить только после готовности ровно четырёх документов"
            )
        for dtype in SELF_FILING_DELIVERABLE_TYPES:
            document = by_type[dtype]
            if (
                document.status != DocumentStatus.APPROVED
                or not document_is_usable(document)
            ):
                raise ValueError(
                    f"Документ {dtype} не прошёл проверку и одобрение"
                )

        message = EmailMessage(policy=SMTP)
        message["Subject"] = (
            f"Судебный комплект — обращение {case.case_number}"
        )
        message["From"] = str(settings.self_filing_smtp_from_email).strip()
        message["To"] = str(package.delivery_email)
        message["Message-ID"] = package.email_message_id or _stable_message_id(package)

        clarification = (
            "Акт передачи квартиры не был подписан на дату оплаты услуги. "
            "Расчёт суммы иска зафиксирован на дату оплаты. Дорожная карта "
            "объясняет, как в судебном заседании уточнить исковые требования "
            "и представить новый расчёт."
            if package.claim_update_in_court_required
            else
            "Акт передачи квартиры подписан. Расчёт суммы иска зафиксирован "
            "на дату подписания акта."
        )
        message.set_content(
            "Готов судебный комплект для самостоятельной подачи.\n\n"
            "В письмо вложены ровно четыре документа:\n"
            "1. Претензия.\n"
            "2. Исковое заявление.\n"
            "3. Расчёт суммы иска.\n"
            "4. Дорожная карта клиента.\n\n"
            f"Обращение: {case.case_number}\n"
            f"Суд, подтверждённый юристом: {package.court_name or 'уточняется'}\n"
            f"Адрес суда: {package.court_address or 'уточняется'}\n"
            f"Дата расчёта суммы иска: "
            f"{package.claim_calculation_cutoff_date.isoformat() if package.claim_calculation_cutoff_date else 'не зафиксирована'}\n\n"
            f"{clarification}\n\n"
            "Представительство в суде в эту услугу не входит. Сохраните письмо "
            "и все четыре вложения; дальнейшие действия выполняйте по дорожной карте."
        )

        storage = LocalStorageService()
        for dtype in SELF_FILING_DELIVERABLE_TYPES:
            document = by_type[dtype]
            plaintext = await asyncio.to_thread(
                storage.read_document_bytes,
                document.file_path,
                expected_case_id=int(case.id),
                expected_sha256=document.sha256,
                encryption_key_id=document.encryption_key_id,
                encryption_envelope_id=document.encryption_envelope_id,
                encrypted_data_key=document.encrypted_data_key,
                encrypted_data_key_nonce=document.encrypted_data_key_nonce,
            )
            mime = str(document.mime_type or "application/octet-stream")
            maintype, subtype = (
                mime.split("/", 1)
                if "/" in mime
                else ("application", "octet-stream")
            )
            filename = Path(str(document.file_name or f"{dtype}.pdf")).name
            message.add_attachment(
                plaintext,
                maintype=maintype,
                subtype=subtype,
                filename=filename,
            )
        return message

    async def send_one(self, package_id: int) -> bool:
        require_email_delivery_configured()
        package = await self._locked_package(package_id)
        if package is None:
            return False
        if package.email_delivery_status == EMAIL_SENT:
            return True
        if package.email_delivery_status not in {EMAIL_QUEUED, EMAIL_FAILED}:
            return False
        if int(package.email_delivery_attempts or 0) >= int(
            settings.self_filing_email_max_attempts
        ):
            return False

        case = await self.db.get(Case, int(package.case_id))
        deliverable_ids = [
            getattr(package, SELF_FILING_DELIVERABLE_FIELDS[dtype])
            for dtype in SELF_FILING_DELIVERABLE_TYPES
        ]
        documents: list[Document] = []
        if case is not None and all(deliverable_ids):
            for document_id in deliverable_ids:
                document = await self.db.get(Document, int(document_id))
                if document is not None:
                    documents.append(document)
        if case is None or len(documents) != len(SELF_FILING_DELIVERABLE_TYPES):
            package.email_delivery_status = EMAIL_FAILED
            package.email_last_error = (
                "Case или один из четырёх документов судебного комплекта не найден"
            )
            return False

        package.email_delivery_attempts = int(package.email_delivery_attempts or 0) + 1
        package.email_delivery_status = EMAIL_SENDING
        package.email_message_id = package.email_message_id or _stable_message_id(package)
        package.email_last_error = None
        await self.db.flush()

        try:
            message = await self._compose(
                package=package,
                case=case,
                documents=documents,
            )
            await asyncio.to_thread(_smtp_send, message)
        except Exception as error:
            package.email_delivery_status = EMAIL_FAILED
            package.email_last_error = (
                f"{type(error).__name__}: {str(error)}"[:1000]
            )
            await add_case_history_event(
                self.db,
                actor_type="system",
                actor_id=None,
                case_id=int(case.id),
                action="SELF_FILING_EMAIL_DELIVERY_FAILED",
                new_value={
                    "package_id": int(package.id),
                    "attempt": int(package.email_delivery_attempts),
                    "error_type": type(error).__name__,
                    "message_id": package.email_message_id,
                },
            )
            await self.notifications.emit(
                event_code="SELF_FILING_EMAIL_DELIVERY_FAILED",
                case_id=int(case.id),
                payload={
                    "case_number": case.case_number,
                    "package_id": int(package.id),
                    "attempt": int(package.email_delivery_attempts),
                },
                dedupe_key=(
                    f"self-filing:{package.id}:email-failed:"
                    f"{int(package.email_delivery_attempts)}"
                ),
            )
            await self.db.flush()
            return False

        sent_at = datetime.now(timezone.utc)
        package.email_delivery_status = EMAIL_SENT
        package.email_sent_at = sent_at
        package.email_last_error = None
        await add_case_history_event(
            self.db,
            actor_type="system",
            actor_id=None,
            case_id=int(case.id),
            action="SELF_FILING_EMAIL_SENT",
            new_value={
                "package_id": int(package.id),
                "attempt": int(package.email_delivery_attempts),
                "message_id": package.email_message_id,
                "sent_at": sent_at.isoformat(),
                "documents": [
                    {
                        "id": int(document.id),
                        "type": str(document.document_type),
                        "sha256": document.sha256,
                    }
                    for document in documents
                ],
            },
        )
        await SelfFilingService(self.db).close_after_delivery(
            case_id=int(case.id),
            message_id=package.email_message_id,
            sent_at=sent_at,
        )
        await self.notifications.emit(
            event_code="SELF_FILING_PACKAGE_DELIVERED",
            case_id=int(case.id),
            user_id=int(case.client_id),
            payload={
                "case_number": case.case_number,
                "delivery_email": package.delivery_email,
                "sent_at": sent_at.isoformat(),
            },
            dedupe_key=f"self-filing:{package.id}:delivered",
        )
        await self.db.flush()
        return True

    async def send_pending(self) -> dict[str, int]:
        if not email_delivery_configured():
            return {"sent": 0, "failed": 0, "blocked_configuration": 1}

        max_attempts = max(1, int(settings.self_filing_email_max_attempts))
        ids = list(
            (
                await self.db.execute(
                    select(SelfFilingPackage.id)
                    .where(
                        SelfFilingPackage.email_delivery_status.in_(
                            {EMAIL_QUEUED, EMAIL_FAILED}
                        ),
                        SelfFilingPackage.email_delivery_attempts < max_attempts,
                    )
                    .order_by(SelfFilingPackage.id.asc())
                    .limit(25)
                )
            ).scalars().all()
        )
        sent = 0
        failed = 0
        for package_id in ids:
            if await self.send_one(int(package_id)):
                sent += 1
            else:
                failed += 1
        return {"sent": sent, "failed": failed, "blocked_configuration": 0}


__all__ = [
    "SelfFilingEmailConfigurationError",
    "SelfFilingEmailSender",
    "email_delivery_configuration_error",
    "email_delivery_configured",
    "require_email_delivery_configured",
    "send_self_filing_email_verification",
]
