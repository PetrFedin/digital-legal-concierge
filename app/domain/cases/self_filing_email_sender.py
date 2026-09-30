from __future__ import annotations

import asyncio
import smtplib
from datetime import datetime, timedelta, timezone
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
    EMAIL_UNKNOWN,
    SelfFilingService,
)
from app.domain.documents.document_service import document_is_usable
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.statuses.document_statuses import DocumentStatus
from app.models.case import Case
from app.models.document import Document
from app.models.self_filing_email_delivery_attempt import SelfFilingEmailDeliveryAttempt
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


class SelfFilingSMTPDefinitiveFailure(RuntimeError):
    """SMTP explicitly rejected the operation; automatic retry is safe."""


class SelfFilingSMTPUnknownOutcome(RuntimeError):
    """SMTP I/O ended without a trustworthy acceptance/rejection receipt."""


class SelfFilingEmailDeliveryUnknownError(RuntimeError):
    """Delivery may have reached the recipient and requires reconciliation."""


ATTEMPT_PREPARED = "PREPARED"
ATTEMPT_SENDING = "SENDING"
ATTEMPT_SENT = "SENT"
ATTEMPT_FAILED = "FAILED"
ATTEMPT_UNKNOWN = "UNKNOWN"


def _stable_message_id(package: SelfFilingPackage, attempt_number: int) -> str:
    from_email = str(settings.self_filing_smtp_from_email or "").strip()
    domain = (
        from_email.rsplit("@", 1)[1]
        if "@" in from_email
        else "legal-concierge.local"
    )
    return (
        f"<self-filing-{int(package.id)}-v{int(package.version or 1)}"
        f"-a{int(attempt_number)}@{domain}>"
    )


def _smtp_send(message: EmailMessage) -> None:
    """Send once and distinguish explicit rejection from ambiguous transport loss."""

    timeout = max(5, int(settings.self_filing_email_timeout_seconds or 30))
    smtp = None
    try:
        try:
            smtp = smtplib.SMTP(
                str(settings.self_filing_smtp_host),
                int(settings.self_filing_smtp_port),
                timeout=timeout,
            )
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
        except SelfFilingEmailConfigurationError:
            raise
        except Exception as error:
            # No DATA/send_message call has happened yet, so no recipient could
            # have accepted this delivery.
            raise SelfFilingSMTPDefinitiveFailure(
                f"{type(error).__name__}: {str(error)}"
            ) from error

        try:
            refused = smtp.send_message(message)
        except (
            smtplib.SMTPRecipientsRefused,
            smtplib.SMTPSenderRefused,
            smtplib.SMTPDataError,
        ) as error:
            # The SMTP server returned an explicit rejection.
            raise SelfFilingSMTPDefinitiveFailure(
                f"{type(error).__name__}: {str(error)}"
            ) from error
        except Exception as error:
            # A socket/protocol failure during DATA can occur after remote
            # acceptance but before our process sees the final reply.
            raise SelfFilingSMTPUnknownOutcome(
                f"{type(error).__name__}: {str(error)}"
            ) from error

        if refused:
            raise SelfFilingSMTPDefinitiveFailure(
                "SMTP refused one or more recipients"
            )
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                try:
                    smtp.close()
                except Exception:
                    pass


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
    """Crash-safe SMTP delivery authority for approved self-filing packages.

    SMTP cannot provide transactional exactly-once semantics. The sender therefore
    freezes one durable PREPARED attempt, commits SENDING before external I/O and
    never automatically retries an ambiguous outcome. A delivery whose outcome
    cannot be proven becomes UNKNOWN and requires an explicit admin decision.
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

    async def _locked_attempt(
        self,
        attempt_id: int,
    ) -> SelfFilingEmailDeliveryAttempt | None:
        return (
            await self.db.execute(
                select(SelfFilingEmailDeliveryAttempt)
                .where(SelfFilingEmailDeliveryAttempt.id == int(attempt_id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def _latest_attempt(
        self,
        package_id: int,
        *,
        for_update: bool = False,
    ) -> SelfFilingEmailDeliveryAttempt | None:
        query = (
            select(SelfFilingEmailDeliveryAttempt)
            .where(SelfFilingEmailDeliveryAttempt.package_id == int(package_id))
            .order_by(
                SelfFilingEmailDeliveryAttempt.attempt_number.desc(),
                SelfFilingEmailDeliveryAttempt.id.desc(),
            )
            .limit(1)
        )
        if for_update:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalars().first()

    @staticmethod
    def _documents_snapshot(documents: list[Document]) -> list[dict[str, object]]:
        by_type = {str(item.document_type): item for item in documents}
        if set(by_type) != set(SELF_FILING_DELIVERABLE_TYPES):
            raise ValueError(
                "Delivery attempt requires exactly the four approved deliverables"
            )
        return [
            {
                "id": int(by_type[dtype].id),
                "type": dtype,
                "sha256": str(by_type[dtype].sha256 or "").lower(),
            }
            for dtype in SELF_FILING_DELIVERABLE_TYPES
        ]

    async def _compose(
        self,
        *,
        package: SelfFilingPackage,
        case: Case,
        documents: list[Document],
        message_id: str,
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
        message["Message-ID"] = message_id

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

    async def _case_and_documents(
        self,
        package: SelfFilingPackage,
    ) -> tuple[Case, list[Document]]:
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
            raise ValueError(
                "Case или один из четырёх документов судебного комплекта не найден"
            )
        return case, documents

    async def _prepare_attempt(
        self,
        *,
        package: SelfFilingPackage,
        case: Case,
        documents: list[Document],
        allow_unknown_retry: bool,
    ) -> tuple[SelfFilingEmailDeliveryAttempt, EmailMessage]:
        snapshot = self._documents_snapshot(documents)
        latest = await self._latest_attempt(int(package.id), for_update=True)

        if latest is not None and latest.state == ATTEMPT_SENT:
            raise SelfFilingEmailDeliveryUnknownError(
                "Email уже имеет подтверждённую SENT-попытку"
            )
        if latest is not None and latest.state == ATTEMPT_SENDING:
            raise SelfFilingEmailDeliveryUnknownError(
                "Email уже отправляется; повторная отправка заблокирована"
            )
        if (
            latest is not None
            and latest.state == ATTEMPT_UNKNOWN
            and not allow_unknown_retry
        ):
            raise SelfFilingEmailDeliveryUnknownError(
                "Исход предыдущей SMTP-попытки неизвестен; автоматический повтор запрещён"
            )

        if (
            latest is not None
            and latest.state == ATTEMPT_PREPARED
            and int(latest.package_version) == int(package.version or 1)
            and str(latest.recipient_email).lower()
            == str(package.delivery_email or "").strip().lower()
            and list(latest.documents_snapshot or []) == snapshot
        ):
            attempt = latest
            message_id = str(attempt.message_id)
        else:
            if latest is not None and latest.state == ATTEMPT_PREPARED:
                # PREPARED has not crossed the external-I/O boundary and can be
                # safely retired if the package snapshot changed.
                latest.state = ATTEMPT_FAILED
                latest.failed_at = datetime.now(timezone.utc)
                latest.last_error = "Package snapshot changed before SMTP I/O"

            attempt_number = max(
                int(package.email_delivery_attempts or 0),
                int(latest.attempt_number or 0) if latest is not None else 0,
            ) + 1
            message_id = _stable_message_id(package, attempt_number)
            attempt = SelfFilingEmailDeliveryAttempt(
                package_id=int(package.id),
                case_id=int(case.id),
                package_version=int(package.version or 1),
                attempt_number=attempt_number,
                recipient_email=str(package.delivery_email or "").strip().lower(),
                message_id=message_id,
                state=ATTEMPT_PREPARED,
                documents_snapshot=snapshot,
                prepared_at=datetime.now(timezone.utc),
            )
            self.db.add(attempt)
            package.email_delivery_attempts = attempt_number

        message = await self._compose(
            package=package,
            case=case,
            documents=documents,
            message_id=message_id,
        )
        package.email_message_id = message_id
        package.email_last_error = None
        await self.db.flush()

        # PREPARED is durable before the process moves toward external I/O. A
        # crash here is safely resumable because SMTP has not been called.
        await self.db.commit()
        return attempt, message

    async def _start_sending(
        self,
        *,
        package_id: int,
        attempt_id: int,
    ) -> tuple[SelfFilingPackage, SelfFilingEmailDeliveryAttempt] | None:
        package = await self._locked_package(package_id)
        if package is None:
            return None
        attempt = await self._locked_attempt(attempt_id)
        if attempt is None:
            return None

        if attempt.state == ATTEMPT_SENDING:
            return None
        if attempt.state != ATTEMPT_PREPARED:
            return None
        if package.email_delivery_status == EMAIL_SENT:
            return None

        attempt.state = ATTEMPT_SENDING
        attempt.sending_at = datetime.now(timezone.utc)
        attempt.last_error = None
        package.email_delivery_status = EMAIL_SENDING
        package.email_message_id = attempt.message_id
        package.email_last_error = None
        await self.db.flush()

        # This is the durable external-side-effect fence. Any worker that starts
        # later observes SENDING and must not call SMTP for this attempt.
        await self.db.commit()
        return package, attempt

    async def _mark_definitive_failure(
        self,
        *,
        package_id: int,
        attempt_id: int,
        case_id: int,
        error: Exception,
    ) -> None:
        await self.db.rollback()
        package = await self._locked_package(package_id)
        attempt = await self._locked_attempt(attempt_id)
        if package is None or attempt is None:
            raise SelfFilingEmailDeliveryUnknownError(
                "Не удалось восстановить durable SMTP attempt после ошибки"
            ) from error
        if attempt.state != ATTEMPT_SENDING:
            raise SelfFilingEmailDeliveryUnknownError(
                "SMTP attempt изменился параллельно; автоматический повтор запрещён"
            ) from error

        failed_at = datetime.now(timezone.utc)
        detail = f"{type(error).__name__}: {str(error)}"[:1000]
        attempt.state = ATTEMPT_FAILED
        attempt.failed_at = failed_at
        attempt.last_error = detail
        package.email_delivery_status = EMAIL_FAILED
        package.email_last_error = detail
        await add_case_history_event(
            self.db,
            actor_type="system",
            actor_id=None,
            case_id=case_id,
            action="SELF_FILING_EMAIL_DELIVERY_FAILED",
            new_value={
                "package_id": int(package.id),
                "attempt_id": int(attempt.id),
                "attempt": int(attempt.attempt_number),
                "error_type": type(error).__name__,
                "message_id": attempt.message_id,
            },
        )
        await self.notifications.emit(
            event_code="SELF_FILING_EMAIL_DELIVERY_FAILED",
            case_id=case_id,
            payload={
                "package_id": int(package.id),
                "attempt": int(attempt.attempt_number),
            },
            dedupe_key=f"self-filing:{package.id}:email-failed:{attempt.id}",
        )
        await self.db.commit()

    async def _mark_unknown(
        self,
        *,
        package_id: int,
        attempt_id: int,
        case_id: int,
        error: Exception,
        reason: str,
    ) -> bool:
        """Persist UNKNOWN unless a preceding SENT commit is already visible."""

        try:
            await self.db.rollback()
        except Exception:
            pass

        package = await self._locked_package(package_id)
        attempt = await self._locked_attempt(attempt_id)
        if package is None or attempt is None:
            raise SelfFilingEmailDeliveryUnknownError(
                "SMTP завершился без доступного durable attempt"
            ) from error

        if attempt.state == ATTEMPT_SENT and package.email_delivery_status == EMAIL_SENT:
            return True
        if attempt.state == ATTEMPT_UNKNOWN:
            return False
        if attempt.state != ATTEMPT_SENDING:
            raise SelfFilingEmailDeliveryUnknownError(
                "SMTP outcome нельзя безопасно классифицировать"
            ) from error

        unknown_at = datetime.now(timezone.utc)
        detail = f"{reason}: {type(error).__name__}: {str(error)}"[:1000]
        attempt.state = ATTEMPT_UNKNOWN
        attempt.unknown_at = unknown_at
        attempt.last_error = detail
        package.email_delivery_status = EMAIL_UNKNOWN
        package.email_last_error = detail
        package.email_message_id = attempt.message_id
        await add_case_history_event(
            self.db,
            actor_type="system",
            actor_id=None,
            case_id=case_id,
            action="SELF_FILING_EMAIL_DELIVERY_UNKNOWN",
            new_value={
                "package_id": int(package.id),
                "attempt_id": int(attempt.id),
                "attempt": int(attempt.attempt_number),
                "message_id": attempt.message_id,
                "unknown_at": unknown_at.isoformat(),
            },
            comment=(
                "Исход SMTP-попытки нельзя подтвердить автоматически; "
                "слепая повторная отправка заблокирована"
            ),
        )
        await self.db.commit()
        return False

    async def _finalize_sent(
        self,
        *,
        package_id: int,
        attempt_id: int,
        case_id: int,
    ) -> bool:
        # Lock Case first to preserve the standard Case -> Package lock order
        # used by SelfFilingService.
        case = (
            await self.db.execute(
                select(Case).where(Case.id == int(case_id)).with_for_update()
            )
        ).scalar_one_or_none()
        if case is None:
            raise LookupError("Дело не найдено после SMTP-отправки")
        package = await self._locked_package(package_id)
        attempt = await self._locked_attempt(attempt_id)
        if package is None or attempt is None:
            raise LookupError("Delivery attempt не найден после SMTP-отправки")

        if attempt.state == ATTEMPT_SENT and package.email_delivery_status == EMAIL_SENT:
            return True
        if attempt.state != ATTEMPT_SENDING:
            raise SelfFilingEmailDeliveryUnknownError(
                "Delivery attempt потерял SENDING fence после SMTP"
            )

        sent_at = datetime.now(timezone.utc)
        attempt.state = ATTEMPT_SENT
        attempt.sent_at = sent_at
        attempt.last_error = None
        package.email_delivery_status = EMAIL_SENT
        package.email_sent_at = sent_at
        package.email_message_id = attempt.message_id
        package.email_last_error = None
        await add_case_history_event(
            self.db,
            actor_type="system",
            actor_id=None,
            case_id=int(case.id),
            action="SELF_FILING_EMAIL_SENT",
            new_value={
                "package_id": int(package.id),
                "attempt_id": int(attempt.id),
                "attempt": int(attempt.attempt_number),
                "message_id": attempt.message_id,
                "sent_at": sent_at.isoformat(),
                "documents": list(attempt.documents_snapshot or []),
            },
        )
        await SelfFilingService(self.db).close_after_delivery(
            case_id=int(case.id),
            message_id=attempt.message_id,
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
        await self.db.commit()
        return True

    async def send_one(
        self,
        package_id: int,
        *,
        allow_unknown_retry: bool = False,
    ) -> bool:
        require_email_delivery_configured()
        package = await self._locked_package(package_id)
        if package is None:
            return False
        if package.email_delivery_status == EMAIL_SENT:
            return True
        if package.email_delivery_status == EMAIL_SENDING:
            return False
        if (
            package.email_delivery_status == EMAIL_UNKNOWN
            and not allow_unknown_retry
        ):
            return False
        if package.email_delivery_status not in {
            EMAIL_QUEUED,
            EMAIL_FAILED,
            EMAIL_UNKNOWN,
        }:
            return False
        if int(package.email_delivery_attempts or 0) >= int(
            settings.self_filing_email_max_attempts
        ):
            return False

        case, documents = await self._case_and_documents(package)
        attempt, message = await self._prepare_attempt(
            package=package,
            case=case,
            documents=documents,
            allow_unknown_retry=allow_unknown_retry,
        )
        started = await self._start_sending(
            package_id=int(package.id),
            attempt_id=int(attempt.id),
        )
        if started is None:
            return False

        try:
            await asyncio.to_thread(_smtp_send, message)
        except SelfFilingSMTPDefinitiveFailure as error:
            await self._mark_definitive_failure(
                package_id=int(package.id),
                attempt_id=int(attempt.id),
                case_id=int(case.id),
                error=error,
            )
            return False
        except Exception as error:
            await self._mark_unknown(
                package_id=int(package.id),
                attempt_id=int(attempt.id),
                case_id=int(case.id),
                error=error,
                reason="SMTP transport outcome is ambiguous",
            )
            raise SelfFilingEmailDeliveryUnknownError(
                "Исход SMTP-попытки неизвестен; автоматический повтор заблокирован"
            ) from error

        try:
            return await self._finalize_sent(
                package_id=int(package.id),
                attempt_id=int(attempt.id),
                case_id=int(case.id),
            )
        except Exception as error:
            committed = await self._mark_unknown(
                package_id=int(package.id),
                attempt_id=int(attempt.id),
                case_id=int(case.id),
                error=error,
                reason="SMTP accepted message but delivery commit did not complete cleanly",
            )
            if committed:
                return True
            raise SelfFilingEmailDeliveryUnknownError(
                "SMTP мог принять письмо, но durable SENT не подтверждён; "
                "повтор требует ручной сверки"
            ) from error

    async def reconcile_stale_sending(self) -> int:
        """Move abandoned SENDING attempts to UNKNOWN, never to auto-retry."""

        stale_seconds = max(
            60,
            int(settings.self_filing_email_timeout_seconds or 30) * 2,
        )
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)
        ids = list(
            (
                await self.db.execute(
                    select(SelfFilingEmailDeliveryAttempt.id)
                    .where(
                        SelfFilingEmailDeliveryAttempt.state == ATTEMPT_SENDING,
                        SelfFilingEmailDeliveryAttempt.sending_at.is_not(None),
                        SelfFilingEmailDeliveryAttempt.sending_at <= cutoff,
                    )
                    .order_by(SelfFilingEmailDeliveryAttempt.id.asc())
                    .limit(100)
                )
            ).scalars().all()
        )

        changed = 0
        for attempt_id in ids:
            probe = await self.db.get(SelfFilingEmailDeliveryAttempt, int(attempt_id))
            if probe is None:
                continue
            package = await self._locked_package(int(probe.package_id))
            attempt = await self._locked_attempt(int(attempt_id))
            if (
                package is None
                or attempt is None
                or attempt.state != ATTEMPT_SENDING
                or attempt.sending_at is None
                or attempt.sending_at > cutoff
            ):
                await self.db.rollback()
                continue

            unknown_at = datetime.now(timezone.utc)
            attempt.state = ATTEMPT_UNKNOWN
            attempt.unknown_at = unknown_at
            attempt.last_error = (
                "Worker stopped before a durable SMTP outcome was recorded"
            )
            if (
                package.email_delivery_status == EMAIL_SENDING
                and package.email_message_id == attempt.message_id
            ):
                package.email_delivery_status = EMAIL_UNKNOWN
                package.email_last_error = attempt.last_error
            await add_case_history_event(
                self.db,
                actor_type="system",
                actor_id=None,
                case_id=int(attempt.case_id),
                action="SELF_FILING_EMAIL_DELIVERY_UNKNOWN",
                new_value={
                    "package_id": int(attempt.package_id),
                    "attempt_id": int(attempt.id),
                    "attempt": int(attempt.attempt_number),
                    "message_id": attempt.message_id,
                    "unknown_at": unknown_at.isoformat(),
                    "reason": "stale_sending_reconciled",
                },
                comment="Stale SENDING переведён в UNKNOWN; автоматический повтор запрещён",
            )
            await self.db.commit()
            changed += 1
        return changed

    async def send_pending(self) -> dict[str, int]:
        if not email_delivery_configured():
            return {
                "sent": 0,
                "failed": 0,
                "unknown_reconciled": 0,
                "blocked_configuration": 1,
            }

        unknown_reconciled = await self.reconcile_stale_sending()
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
            try:
                if await self.send_one(int(package_id)):
                    sent += 1
                else:
                    failed += 1
            except SelfFilingEmailDeliveryUnknownError:
                # UNKNOWN is intentionally not put back into the automatic queue.
                failed += 1
        return {
            "sent": sent,
            "failed": failed,
            "unknown_reconciled": unknown_reconciled,
            "blocked_configuration": 0,
        }


__all__ = [
    "SelfFilingEmailConfigurationError",
    "SelfFilingEmailDeliveryUnknownError",
    "SelfFilingEmailSender",
    "SelfFilingSMTPDefinitiveFailure",
    "SelfFilingSMTPUnknownOutcome",
    "email_delivery_configuration_error",
    "email_delivery_configured",
    "require_email_delivery_configured",
    "send_self_filing_email_verification",
]
