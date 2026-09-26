from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.cases.self_filing_business_calendar import (
    add_business_days,
    load_business_calendar,
)
from app.domain.cases.self_filing_contract import (
    SELF_FILING_EMAIL_VERIFICATION_MAX_ATTEMPTS,
    SELF_FILING_EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS,
    SELF_FILING_EMAIL_VERIFICATION_PBKDF2_ROUNDS,
    SELF_FILING_EMAIL_VERIFICATION_TTL_MINUTES,
    SELF_FILING_PRICE_RUB,
    SELF_FILING_SLA_BUSINESS_DAYS,
)
from app.domain.cases.service_modes import M1ServiceMode
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.documents.document_service import (
    DocumentService,
    document_is_usable,
)
from app.domain.payments.payment_lifecycle import PaymentLifecycleService
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.document_statuses import DocumentStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.document import Document
from app.models.payment import Payment
from app.models.self_filing_package import SelfFilingPackage
from app.system.settings_service import SettingsService


SELF_FILING_REQUIRED_TYPES = frozenset({"DDU", "PASSPORT"})
SELF_FILING_STATUS_PROFILE_PENDING = "PROFILE_PENDING"
SELF_FILING_STATUS_DOCUMENTS_PENDING = "DOCUMENTS_PENDING"
SELF_FILING_STATUS_DOCUMENTS_RECEIVED = "DOCUMENTS_RECEIVED"
SELF_FILING_STATUS_LAWYER_REVIEW = "LAWYER_REVIEW"
SELF_FILING_STATUS_DOCS_REQUESTED = "DOCS_REQUESTED"
SELF_FILING_STATUS_PAYMENT_PENDING = "PAYMENT_PENDING"
SELF_FILING_STATUS_PREPARATION = "PREPARATION"
SELF_FILING_STATUS_READY = "READY"
SELF_FILING_STATUS_DELIVERED = "DELIVERED"
SELF_FILING_STATUS_CLOSED = "CLOSED"

EMAIL_NOT_QUEUED = "NOT_QUEUED"
EMAIL_QUEUED = "QUEUED"
EMAIL_SENDING = "SENDING"
EMAIL_SENT = "SENT"
EMAIL_FAILED = "FAILED"

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

JURISDICTION_BASES = frozenset(
    {
        "CLIENT_RESIDENCE_OR_STAY",
        "DEFENDANT_LOCATION",
        "CONTRACT_CONCLUSION_OR_PERFORMANCE",
        "BRANCH_LOCATION",
        "OTHER_LAWYER_CONFIRMED",
    }
)


class SelfFilingError(ValueError):
    pass


class SelfFilingEmailVerificationError(SelfFilingError):
    def __init__(self, message: str, *, persist_state: bool = False):
        super().__init__(message)
        self.persist_state = bool(persist_state)


def _email_code_hash(*, code: str, salt_hex: str) -> str:
    try:
        salt = bytes.fromhex(str(salt_hex))
    except ValueError as error:
        raise SelfFilingError("Повреждены данные проверки email") from error
    return hashlib.pbkdf2_hmac(
        "sha256",
        str(code).encode("utf-8"),
        salt,
        SELF_FILING_EMAIL_VERIFICATION_PBKDF2_ROUNDS,
    ).hex()


def _clear_email_verification_challenge(
    package: SelfFilingPackage,
    *,
    reset_attempts: bool = True,
) -> None:
    package.email_verification_salt = None
    package.email_verification_hash = None
    package.email_verification_expires_at = None
    if reset_attempts:
        package.email_verification_attempts = 0


class SelfFilingService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.cases = CaseService(db)
        self.documents = DocumentService(db)
        self.payments = PaymentService(db)
        self.notifications = NotificationEngine(db)

    async def _require_commercial_contract(
        self,
        *,
        payment: Payment | None = None,
    ) -> int:
        settings_service = SettingsService(self.db)
        configured_price = Decimal(
            str(await settings_service.get_value("payments.m1_self_filing_package"))
        )
        configured_days = int(
            await settings_service.get_value("self_filing.sla_business_days")
        )
        if configured_price != SELF_FILING_PRICE_RUB:
            raise SelfFilingError(
                "Стоимость услуги в настройках не соответствует согласованным "
                f"{SELF_FILING_PRICE_RUB:.0f} ₽. Открытие или применение оплаты заблокировано."
            )
        if configured_days != SELF_FILING_SLA_BUSINESS_DAYS:
            raise SelfFilingError(
                "SLA услуги в настройках не соответствует согласованным "
                f"{SELF_FILING_SLA_BUSINESS_DAYS} рабочим дням. "
                "Открытие или применение оплаты заблокировано."
            )
        if payment is not None and Decimal(str(payment.amount)) != SELF_FILING_PRICE_RUB:
            raise SelfFilingError(
                "Сумма полученного платежа не соответствует коммерческому контракту "
                f"{SELF_FILING_PRICE_RUB:.0f} ₽; требуется финансовая сверка."
            )
        return configured_days

    async def _issue_email_verification(
        self,
        *,
        case: Case,
        package: SelfFilingPackage,
        actor_type: str,
        actor_id: int | None,
    ) -> SelfFilingPackage:
        if not package.delivery_email:
            raise SelfFilingError("Email для проверки не задан")

        from app.domain.cases.self_filing_email_sender import (
            SelfFilingEmailConfigurationError,
            send_self_filing_email_verification,
        )

        code = f"{secrets.randbelow(1_000_000):06d}"
        salt_hex = secrets.token_bytes(16).hex()
        issued_at = datetime.now(timezone.utc)
        expires_at = issued_at + timedelta(
            minutes=SELF_FILING_EMAIL_VERIFICATION_TTL_MINUTES
        )

        package.email_confirmed_at = None
        package.email_verification_salt = salt_hex
        package.email_verification_hash = _email_code_hash(
            code=code,
            salt_hex=salt_hex,
        )
        package.email_verification_expires_at = expires_at
        package.email_verification_attempts = 0
        package.email_verification_sent_at = None
        package.email_verification_message_id = None
        package.version = int(package.version or 1) + 1

        try:
            message_id = await send_self_filing_email_verification(
                to_email=str(package.delivery_email),
                code=code,
                case_number=str(case.case_number),
                package_id=int(package.id),
                challenge_version=int(package.version),
            )
        except SelfFilingEmailConfigurationError as error:
            raise SelfFilingError(
                "Проверка email временно недоступна: канал отправки не настроен. "
                "Данные не сохранены и оплата не открыта."
            ) from error
        except Exception as error:
            raise SelfFilingError(
                "Не удалось отправить код подтверждения email. "
                "Данные не сохранены; повторите попытку позже."
            ) from error

        package.email_verification_sent_at = issued_at
        package.email_verification_message_id = message_id
        email = str(package.delivery_email).lower()
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=int(case.id),
            action="SELF_FILING_EMAIL_VERIFICATION_SENT",
            new_value={
                "package_id": int(package.id),
                "email_sha256": hashlib.sha256(email.encode("utf-8")).hexdigest(),
                "email_domain": email.rsplit("@", 1)[-1] if "@" in email else None,
                "message_id": message_id,
                "sent_at": issued_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "max_attempts": SELF_FILING_EMAIL_VERIFICATION_MAX_ATTEMPTS,
            },
        )
        await self.db.flush()
        return package

    async def resend_delivery_email_verification(
        self,
        *,
        case_id: int,
        client_id: int,
    ) -> SelfFilingPackage:
        case = await self._lock_case(case_id)
        if int(case.client_id) != int(client_id):
            raise SelfFilingError("Обращение принадлежит другому клиенту")
        if self._case_status(case) != CaseStatus.M1_SELF_FILING_PROFILE_PENDING:
            raise SelfFilingError("Проверка email уже завершена или этап изменился")
        package = await self.require_package(case_id=case.id, for_update=True)
        if not package.delivery_email:
            raise SelfFilingError("Сначала укажите email")
        if package.email_verification_sent_at is not None:
            sent_at = package.email_verification_sent_at
            if sent_at.tzinfo is None:
                sent_at = sent_at.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - sent_at).total_seconds()
            if elapsed < SELF_FILING_EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS:
                wait_seconds = max(
                    1,
                    int(
                        SELF_FILING_EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS
                        - elapsed
                    ),
                )
                raise SelfFilingError(
                    f"Новый код можно запросить через {wait_seconds} сек."
                )
        return await self._issue_email_verification(
            case=case,
            package=package,
            actor_type="client",
            actor_id=int(client_id),
        )

    async def verify_delivery_email(
        self,
        *,
        case_id: int,
        client_id: int,
        code: str,
    ) -> SelfFilingPackage:
        case = await self._lock_case(case_id)
        if int(case.client_id) != int(client_id):
            raise SelfFilingError("Обращение принадлежит другому клиенту")

        package = await self.require_package(case_id=case.id, for_update=True)
        status = self._case_status(case)
        if (
            package.email_confirmed_at is not None
            and status == CaseStatus.M1_SELF_FILING_DOCUMENTS_PENDING
        ):
            return package
        if status != CaseStatus.M1_SELF_FILING_PROFILE_PENDING:
            raise SelfFilingError("Проверка email недоступна на текущем этапе")

        clean_code = str(code or "").strip()
        if len(clean_code) != 6 or not clean_code.isdigit():
            raise SelfFilingEmailVerificationError(
                "Введите шестизначный код из письма."
            )
        if (
            not package.email_verification_salt
            or not package.email_verification_hash
            or package.email_verification_expires_at is None
        ):
            raise SelfFilingEmailVerificationError(
                "Активного кода нет. Запросите новый код."
            )

        now = datetime.now(timezone.utc)
        expires_at = package.email_verification_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if now > expires_at:
            await add_case_history_event(
                self.db,
                actor_type="client",
                actor_id=int(client_id),
                case_id=int(case.id),
                action="SELF_FILING_EMAIL_VERIFICATION_EXPIRED",
                new_value={
                    "package_id": int(package.id),
                    "expires_at": expires_at.isoformat(),
                    "verification_message_id": package.email_verification_message_id,
                },
            )
            _clear_email_verification_challenge(package)
            package.version = int(package.version or 1) + 1
            raise SelfFilingEmailVerificationError(
                "Срок действия кода истёк. Запросите новый код.",
                persist_state=True,
            )

        expected = _email_code_hash(
            code=clean_code,
            salt_hex=str(package.email_verification_salt),
        )
        if not hmac.compare_digest(
            expected,
            str(package.email_verification_hash),
        ):
            package.email_verification_attempts = (
                int(package.email_verification_attempts or 0) + 1
            )
            attempt = int(package.email_verification_attempts)
            remaining = SELF_FILING_EMAIL_VERIFICATION_MAX_ATTEMPTS - attempt
            await add_case_history_event(
                self.db,
                actor_type="client",
                actor_id=int(client_id),
                case_id=int(case.id),
                action="SELF_FILING_EMAIL_VERIFICATION_FAILED",
                new_value={
                    "package_id": int(package.id),
                    "attempt": attempt,
                    "remaining_attempts": max(remaining, 0),
                    "verification_message_id": package.email_verification_message_id,
                },
            )
            if remaining <= 0:
                _clear_email_verification_challenge(
                    package,
                    reset_attempts=False,
                )
                package.version = int(package.version or 1) + 1
                raise SelfFilingEmailVerificationError(
                    "Лимит попыток исчерпан. Запросите новый код.",
                    persist_state=True,
                )
            raise SelfFilingEmailVerificationError(
                f"Код не подошёл. Осталось попыток: {remaining}.",
                persist_state=True,
            )

        package.email_confirmed_at = now
        _clear_email_verification_challenge(package)
        package.status = SELF_FILING_STATUS_DOCUMENTS_PENDING
        package.version = int(package.version or 1) + 1
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_SELF_FILING_DOCUMENTS_PENDING,
            actor_type="client",
            actor_id=int(client_id),
            comment=(
                "Клиент подтвердил владение email одноразовым кодом. "
                "Конкретный суд по адресу автоматически не выбирается."
            ),
        )
        email = str(package.delivery_email or "").lower()
        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=int(client_id),
            case_id=int(case.id),
            action="SELF_FILING_EMAIL_VERIFIED",
            new_value={
                "package_id": int(package.id),
                "email_sha256": hashlib.sha256(email.encode("utf-8")).hexdigest(),
                "email_domain": email.rsplit("@", 1)[-1] if "@" in email else None,
                "verified_at": now.isoformat(),
                "verification_message_id": package.email_verification_message_id,
            },
        )
        await self.db.flush()
        return package

    @staticmethod
    def _case_status(case: Case) -> CaseStatus:
        return case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))

    @staticmethod
    def _normalize_email(value: str) -> str:
        clean = str(value or "").strip().lower()
        if len(clean) > 320 or not _EMAIL_RE.fullmatch(clean):
            raise SelfFilingError("Укажите корректный email для получения готового пакета")
        return clean

    @staticmethod
    def _clean_required(value: str, *, title: str, limit: int) -> str:
        clean = " ".join(str(value or "").split())
        if not clean:
            raise SelfFilingError(f"{title}: значение обязательно")
        if len(clean) > limit:
            raise SelfFilingError(f"{title}: значение слишком длинное")
        return clean

    async def _lock_case(self, case_id: int) -> Case:
        case = (
            await self.db.execute(
                select(Case)
                .where(Case.id == int(case_id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if case is None:
            raise LookupError("Обращение не найдено")
        return case

    async def _lock_payment(self, payment_id: int) -> Payment:
        payment = (
            await self.db.execute(
                select(Payment)
                .where(Payment.id == int(payment_id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if payment is None:
            raise LookupError("Платёж не найден")
        return payment

    async def _package_for_case(
        self,
        *,
        case_id: int,
        create: bool = False,
        for_update: bool = False,
    ) -> SelfFilingPackage | None:
        query = select(SelfFilingPackage).where(
            SelfFilingPackage.case_id == int(case_id)
        )
        if for_update:
            query = query.with_for_update()
        package = (await self.db.execute(query)).scalar_one_or_none()
        if package is None and create:
            package = SelfFilingPackage(
                case_id=int(case_id),
                status=SELF_FILING_STATUS_PROFILE_PENDING,
            )
            self.db.add(package)
            await self.db.flush()
        return package

    async def require_package(
        self,
        *,
        case_id: int,
        for_update: bool = False,
    ) -> SelfFilingPackage:
        package = await self._package_for_case(
            case_id=case_id,
            for_update=for_update,
        )
        if package is None:
            raise SelfFilingError("Карточка услуги самостоятельной подачи не создана")
        return package

    async def start(
        self,
        *,
        case: Case,
        client_id: int,
    ) -> tuple[Case, SelfFilingPackage]:
        case = await self._lock_case(case.id)
        if self._case_status(case) != CaseStatus.CLIENT_DECISION:
            raise SelfFilingError("Самостоятельную подачу можно выбрать только после сохранённого расчёта")
        case.service_mode = M1ServiceMode.SELF_FILING_PACKAGE.value
        package = await self._package_for_case(case_id=case.id, create=True, for_update=True)
        assert package is not None
        package.status = SELF_FILING_STATUS_PROFILE_PENDING
        package.version = int(package.version or 1) + 1
        case = await self.cases.start_self_filing(
            case=case,
            actor_type="client",
            actor_id=int(client_id),
            comment=(
                "Клиент выбрал подготовку документов для самостоятельной подачи в суд; "
                "представительство в суде не включено"
            ),
        )
        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=int(client_id),
            case_id=case.id,
            action="SELF_FILING_SERVICE_MODE_SELECTED",
            new_value={
                "service_mode": M1ServiceMode.SELF_FILING_PACKAGE.value,
                "price_setting": "payments.m1_self_filing_package",
                "sla_setting": "self_filing.sla_business_days",
            },
        )
        await self.db.flush()
        return case, package

    async def save_confirmed_profile(
        self,
        *,
        case_id: int,
        client_id: int,
        region: str,
        address: str,
        email: str,
    ) -> SelfFilingPackage:
        case = await self._lock_case(case_id)
        if int(case.client_id) != int(client_id):
            raise SelfFilingError("Обращение принадлежит другому клиенту")
        if str(case.service_mode or "") != M1ServiceMode.SELF_FILING_PACKAGE.value:
            raise SelfFilingError("Для обращения не выбрана услуга самостоятельной подачи")
        if self._case_status(case) != CaseStatus.M1_SELF_FILING_PROFILE_PENDING:
            raise SelfFilingError("Контактные данные уже приняты или этап изменился")

        package = await self.require_package(case_id=case.id, for_update=True)
        clean_region = self._clean_required(
            region, title="Регион", limit=255
        )
        clean_address = self._clean_required(
            address, title="Адрес клиента", limit=2000
        )
        clean_email = self._normalize_email(email)

        # A double tap of the exact confirmation screen may arrive as two
        # distinct Telegram callbacks. The package row lock serializes them;
        # if the first request already created a still-active challenge for the
        # same facts, the second request is a pure idempotent read and must not
        # invalidate the first code by sending another one.
        active_expires = package.email_verification_expires_at
        if active_expires is not None and active_expires.tzinfo is None:
            active_expires = active_expires.replace(tzinfo=timezone.utc)
        if (
            str(package.client_region or "") == clean_region
            and str(package.client_address or "") == clean_address
            and str(package.delivery_email or "").lower() == clean_email
            and package.email_confirmed_at is None
            and bool(package.email_verification_hash)
            and package.email_verification_sent_at is not None
            and bool(package.email_verification_message_id)
            and active_expires is not None
            and datetime.now(timezone.utc) <= active_expires
        ):
            return package

        package.client_region = clean_region
        package.client_address = clean_address
        package.delivery_email = clean_email
        package.email_confirmed_at = None
        package.status = SELF_FILING_STATUS_PROFILE_PENDING
        package.version = int(package.version or 1) + 1
        _clear_email_verification_challenge(package)

        normalized_email = str(package.delivery_email).lower()
        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=int(client_id),
            case_id=case.id,
            action="SELF_FILING_PROFILE_SAVED_PENDING_EMAIL_VERIFICATION",
            new_value={
                "package_id": int(package.id),
                "client_region": package.client_region,
                "email_sha256": hashlib.sha256(
                    normalized_email.encode("utf-8")
                ).hexdigest(),
                "email_domain": (
                    normalized_email.rsplit("@", 1)[-1]
                    if "@" in normalized_email
                    else None
                ),
                "court_auto_selected": False,
                "email_verified": False,
            },
        )
        return await self._issue_email_verification(
            case=case,
            package=package,
            actor_type="client",
            actor_id=int(client_id),
        )

    async def submit_documents(
        self,
        *,
        case_id: int,
        client_id: int,
    ) -> int:
        case = await self._lock_case(case_id)
        if int(case.client_id) != int(client_id):
            raise SelfFilingError("Обращение принадлежит другому клиенту")
        if self._case_status(case) not in {
            CaseStatus.M1_SELF_FILING_DOCUMENTS_PENDING,
            CaseStatus.M1_SELF_FILING_DOCS_REQUESTED,
        }:
            raise SelfFilingError("Передача комплекта недоступна на текущем этапе")

        submitted = await self.documents.send_documents_to_review(
            case=case,
            actor_id=int(client_id),
            required_types=set(SELF_FILING_REQUIRED_TYPES),
        )
        package = await self.require_package(case_id=case.id, for_update=True)
        package.status = SELF_FILING_STATUS_DOCUMENTS_RECEIVED
        package.version = int(package.version or 1) + 1
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_SELF_FILING_DOCUMENTS_RECEIVED,
            actor_type="client",
            actor_id=int(client_id),
            comment=(
                "Клиент передал документы для самостоятельной подачи. "
                "Машинный минимум DDU + PASSPORT выполнен; полноту приложений "
                "подтверждает юрист отдельно."
            ),
        )
        return submitted

    async def begin_lawyer_review(
        self,
        *,
        case_id: int,
        lawyer_id: int,
    ) -> SelfFilingPackage:
        case = await self._lock_case(case_id)
        if self._case_status(case) not in {
            CaseStatus.M1_SELF_FILING_DOCUMENTS_RECEIVED,
            CaseStatus.M1_SELF_FILING_DOCS_REQUESTED,
        }:
            raise SelfFilingError("Комплект ещё не готов к проверке")
        package = await self.require_package(case_id=case.id, for_update=True)
        package.status = SELF_FILING_STATUS_LAWYER_REVIEW
        package.version = int(package.version or 1) + 1
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_SELF_FILING_LAWYER_REVIEW,
            actor_type="lawyer",
            actor_id=int(lawyer_id),
            comment="Юрист начал проверку комплекта самостоятельной подачи",
        )
        return package

    async def request_more_documents(
        self,
        *,
        case_id: int,
        lawyer_id: int,
        reason: str,
    ) -> SelfFilingPackage:
        case = await self._lock_case(case_id)
        if self._case_status(case) not in {
            CaseStatus.M1_SELF_FILING_DOCUMENTS_RECEIVED,
            CaseStatus.M1_SELF_FILING_LAWYER_REVIEW,
        }:
            raise SelfFilingError("Запрос документов недоступен на текущем этапе")
        clean_reason = self._clean_required(reason, title="Причина запроса", limit=2000)
        package = await self.require_package(case_id=case.id, for_update=True)
        package.status = SELF_FILING_STATUS_DOCS_REQUESTED
        package.documents_complete_at = None
        package.documents_complete_by_lawyer_id = None
        package.version = int(package.version or 1) + 1
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_SELF_FILING_DOCS_REQUESTED,
            actor_type="lawyer",
            actor_id=int(lawyer_id),
            comment=clean_reason,
        )
        return package

    async def _approved_document_gate(self, *, case_id: int) -> list[Document]:
        documents = list(
            (
                await self.db.execute(
                    select(Document)
                    .where(
                        Document.case_id == int(case_id),
                        Document.status != DocumentStatus.ARCHIVED,
                    )
                    .order_by(Document.id.asc())
                    .with_for_update()
                )
            ).scalars().all()
        )
        approved = [
            item
            for item in documents
            if item.status == DocumentStatus.APPROVED and document_is_usable(item)
        ]
        approved_types = {item.document_type for item in approved}
        missing = sorted(SELF_FILING_REQUIRED_TYPES - approved_types)
        if missing:
            raise SelfFilingError(
                "Юрист не может подтвердить комплект: не приняты обязательные документы "
                + ", ".join(missing)
            )
        unresolved = [
            item
            for item in documents
            if item.status != DocumentStatus.APPROVED
        ]
        if unresolved:
            raise SelfFilingError(
                "Юрист не может подтвердить полный комплект, пока есть документы "
                "без итогового статуса APPROVED"
            )
        return approved

    async def approve_for_payment(
        self,
        *,
        case_id: int,
        lawyer_id: int,
        court_name: str,
        court_address: str,
        jurisdiction_basis: str,
        jurisdiction_note: str,
        completeness_confirmed: bool,
    ):
        case = await self._lock_case(case_id)
        if self._case_status(case) not in {
            CaseStatus.M1_SELF_FILING_DOCUMENTS_RECEIVED,
            CaseStatus.M1_SELF_FILING_LAWYER_REVIEW,
        }:
            raise SelfFilingError("Комплект нельзя подтвердить на текущем этапе")
        package = await self.require_package(case_id=case.id, for_update=True)
        if not package.email_confirmed_at or not package.delivery_email:
            raise SelfFilingError("Клиент ещё не подтвердил email для выдачи пакета")
        if completeness_confirmed is not True:
            raise SelfFilingError(
                "Юрист должен явно подтвердить, что по материалам обращения проверены "
                "ДДУ, документ личности, все имеющиеся приложения и дополнительные "
                "соглашения, а также все иные документы, необходимые для подготовки пакета"
            )

        # Machine checks can prove that known files are approved; they cannot
        # prove that an appendix which was never uploaded does not exist. The
        # explicit lawyer attestation above owns that legal completeness fact.
        await self._approved_document_gate(case_id=case.id)

        basis = str(jurisdiction_basis or "").strip().upper()
        if basis not in JURISDICTION_BASES:
            raise SelfFilingError("Неизвестное основание подсудности")
        clean_court = self._clean_required(court_name, title="Суд", limit=500)
        clean_address = self._clean_required(
            court_address, title="Адрес суда", limit=2000
        )
        clean_note = self._clean_required(
            jurisdiction_note, title="Обоснование подсудности", limit=4000
        )

        # Do not expose/take the 15k obligation while either delivery or
        # the promised two-business-day calendar is operationally impossible.
        from app.domain.cases.self_filing_email_sender import (
            require_email_delivery_configured,
        )

        require_email_delivery_configured()
        now = datetime.now(timezone.utc)
        business_days = await self._require_commercial_contract()
        calendar = await load_business_calendar(self.db)
        # This is a preflight for a payment confirmed now. The authoritative
        # SLA is recomputed from the actual paid/completeness timestamps when
        # money is confirmed.
        add_business_days(
            now,
            business_days=business_days,
            calendar=calendar,
        )

        package.documents_complete_at = now
        package.documents_complete_by_lawyer_id = int(lawyer_id)
        package.court_name = clean_court
        package.court_address = clean_address
        package.jurisdiction_basis = basis
        package.jurisdiction_note = clean_note
        package.jurisdiction_confirmed_at = now
        package.jurisdiction_confirmed_by_lawyer_id = int(lawyer_id)
        package.status = SELF_FILING_STATUS_PAYMENT_PENDING
        package.version = int(package.version or 1) + 1

        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_SELF_FILING_PAYMENT_PENDING,
            actor_type="lawyer",
            actor_id=int(lawyer_id),
            comment=(
                "Юрист подтвердил полный комплект документов и конкретную "
                "подсудность для самостоятельной подачи"
            ),
        )
        payment = await self.payments.get_or_create_payment(
            case=case,
            payment_code=PaymentCode.M1_SELF_FILING_PACKAGE,
        )
        await add_case_history_event(
            self.db,
            actor_type="lawyer",
            actor_id=int(lawyer_id),
            case_id=case.id,
            action="SELF_FILING_DOCUMENTS_AND_JURISDICTION_CONFIRMED",
            new_value={
                "package_id": int(package.id),
                "documents_complete_at": now.isoformat(),
                "court_name": clean_court,
                "court_address": clean_address,
                "jurisdiction_basis": basis,
                "lawyer_completeness_attested": True,
                "completeness_scope": (
                    "DDU + identity + all known appendices/additional agreements "
                    "+ other materials required by lawyer"
                ),
                "payment_id": int(payment.id),
                "payment_amount": str(payment.amount),
            },
            comment=clean_note,
        )
        await self.db.flush()
        return package, payment

    async def start_preparation_after_payment(
        self,
        *,
        case: Case,
        payment,
        actor_type: str,
        actor_id: int | None,
        occurred_at: datetime | None = None,
    ) -> SelfFilingPackage:
        case = await self._lock_case(case.id)
        if self._case_status(case) != CaseStatus.M1_SELF_FILING_PAYMENT_PENDING:
            raise SelfFilingError("Оплата не соответствует текущему этапу самостоятельной подачи")
        if str(case.service_mode or "") != M1ServiceMode.SELF_FILING_PACKAGE.value:
            raise SelfFilingError("Оплата относится к другому режиму услуги")
        if str(payment.payment_code) != PaymentCode.M1_SELF_FILING_PACKAGE.value:
            raise SelfFilingError("Некорректный код платежа самостоятельной подачи")
        if str(payment.status) != PaymentStatus.PAID.value:
            raise SelfFilingError("Платёж ещё не подтверждён как полученный")

        package = await self.require_package(case_id=case.id, for_update=True)
        # Re-check delivery capability at the exact money-confirmation boundary.
        # It may have changed after the lawyer opened the payment obligation.
        from app.domain.cases.self_filing_email_sender import (
            require_email_delivery_configured,
        )

        require_email_delivery_configured()
        if not package.documents_complete_at:
            raise SelfFilingError("Полнота документов не подтверждена юристом")
        if not package.jurisdiction_confirmed_at or not package.court_name:
            raise SelfFilingError("Подсудность не подтверждена юристом")
        if not package.email_confirmed_at or not package.delivery_email:
            raise SelfFilingError("Email клиента не подтверждён")

        payment_at = occurred_at or payment.paid_at or datetime.now(timezone.utc)
        completeness_at = package.documents_complete_at
        if completeness_at.tzinfo is None:
            completeness_at = completeness_at.replace(tzinfo=timezone.utc)
        if payment_at.tzinfo is None:
            payment_at = payment_at.replace(tzinfo=timezone.utc)
        started_at = max(payment_at, completeness_at)

        business_days = await self._require_commercial_contract(
            payment=payment,
        )
        calendar = await load_business_calendar(self.db)
        due_at = add_business_days(
            started_at,
            business_days=business_days,
            calendar=calendar,
        )

        package.payment_confirmed_at = payment_at
        package.sla_started_at = started_at
        package.sla_due_at = due_at
        package.status = SELF_FILING_STATUS_PREPARATION
        package.version = int(package.version or 1) + 1

        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_SELF_FILING_PREPARATION,
            actor_type="system",
            actor_id=None,
            comment=(
                f"Оплата пакета подтверждена; SLA {business_days} рабочих дня/дней "
                f"начат до {due_at.isoformat()}"
            ),
        )
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="SELF_FILING_SLA_STARTED",
            new_value={
                "package_id": int(package.id),
                "payment_id": int(payment.id),
                "payment_confirmed_at": payment_at.isoformat(),
                "documents_complete_at": completeness_at.isoformat(),
                "sla_started_at": started_at.isoformat(),
                "sla_due_at": due_at.isoformat(),
                "business_days": business_days,
                "calendar_coverage_through": calendar.coverage_through.isoformat(),
            },
        )
        await self.db.flush()
        return package

    async def resolve_received_payment_review(
        self,
        *,
        case_id: int,
        payment_id: int,
        actor_id: int,
        decision: str,
        comment: str,
    ) -> tuple[SelfFilingPackage, Payment]:
        """Resolve received money that could not atomically start package work.

        Only an administrator should call this method. It never invents money
        truth: PAID_REVIEW already proves that the provider/offline authority
        recorded received funds. A resume reuses the immutable paid_at timestamp;
        a refund decision moves only the financial record to REFUND_PENDING.
        """

        clean_comment = " ".join(str(comment or "").split())
        if len(clean_comment) < 10:
            raise SelfFilingError(
                "Для финансовой сверки нужен содержательный комментарий минимум 10 символов"
            )

        normalized = str(decision or "").strip().lower()
        if normalized not in {"resume", "refund_pending"}:
            raise SelfFilingError(
                "Решение должно быть resume или refund_pending"
            )

        case = await self._lock_case(case_id)
        if str(case.service_mode or "") != M1ServiceMode.SELF_FILING_PACKAGE.value:
            raise SelfFilingError("Обращение относится к другому режиму услуги")
        if self._case_status(case) not in {
            CaseStatus.M1_SELF_FILING_PAYMENT_PENDING,
            CaseStatus.M1_SELF_FILING_PREPARATION,
        }:
            raise SelfFilingError(
                "Финансовая сверка не соответствует текущему этапу пакета"
            )

        package = await self.require_package(case_id=case.id, for_update=True)
        payment = await self._lock_payment(payment_id)
        if int(payment.case_id) != int(case.id):
            raise SelfFilingError("Платёж относится к другому обращению")
        if str(payment.payment_code) != PaymentCode.M1_SELF_FILING_PACKAGE.value:
            raise SelfFilingError("Это не платёж за пакет самостоятельной подачи")

        current_status = PaymentStatus(str(payment.status))
        if normalized == "resume":
            if (
                current_status == PaymentStatus.PAID
                and self._case_status(case)
                == CaseStatus.M1_SELF_FILING_PREPARATION
                and package.sla_started_at is not None
            ):
                return package, payment
            if current_status != PaymentStatus.PAID_REVIEW:
                raise SelfFilingError(
                    "Платёж не находится в статусе PAID_REVIEW"
                )
            if self._case_status(case) != CaseStatus.M1_SELF_FILING_PAYMENT_PENDING:
                raise SelfFilingError(
                    "Возобновление возможно только пока пакет ожидает финансовую сверку"
                )

            money_received_at = payment.paid_at
            if money_received_at is None:
                raise SelfFilingError(
                    "Для PAID_REVIEW отсутствует подтверждённое время получения денег"
                )
            transition = PaymentLifecycleService.transition(
                payment,
                to_status=PaymentStatus.PAID,
                occurred_at=money_received_at,
            )
            package = await self.start_preparation_after_payment(
                case=case,
                payment=payment,
                actor_type="admin",
                actor_id=int(actor_id),
                occurred_at=money_received_at,
            )
            await add_case_history_event(
                self.db,
                actor_type="admin",
                actor_id=int(actor_id),
                case_id=case.id,
                action="SELF_FILING_PAYMENT_REVIEW_RESOLVED",
                old_value={
                    "payment_id": int(payment.id),
                    "payment_status": transition.old_status.value,
                },
                new_value={
                    "payment_id": int(payment.id),
                    "payment_status": transition.new_status.value,
                    "decision": "resume",
                    "money_received_at": money_received_at.isoformat(),
                    "sla_started_at": (
                        package.sla_started_at.isoformat()
                        if package.sla_started_at
                        else None
                    ),
                    "sla_due_at": (
                        package.sla_due_at.isoformat()
                        if package.sla_due_at
                        else None
                    ),
                },
                comment=clean_comment,
            )
            await self.notifications.emit(
                event_code="SELF_FILING_PAYMENT_REVIEW_RESOLVED",
                case_id=case.id,
                user_id=case.client_id,
                payload={
                    "case_number": case.case_number,
                    "payment_id": int(payment.id),
                    "decision": "resume",
                    "sla_due_at": (
                        package.sla_due_at.isoformat()
                        if package.sla_due_at
                        else None
                    ),
                },
                dedupe_key=f"payment:{payment.id}:self-filing-review-resume",
            )
            await self.db.flush()
            return package, payment

        if current_status == PaymentStatus.REFUND_PENDING:
            return package, payment
        if current_status != PaymentStatus.PAID_REVIEW:
            raise SelfFilingError(
                "На возврат можно направить только платёж PAID_REVIEW"
            )

        transition = PaymentLifecycleService.transition(
            payment,
            to_status=PaymentStatus.REFUND_PENDING,
            occurred_at=payment.paid_at,
        )
        case.next_action = "Ожидать завершения возврата 15 000 ₽"
        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=int(actor_id),
            case_id=case.id,
            action="SELF_FILING_PAYMENT_REVIEW_RESOLVED",
            old_value={
                "payment_id": int(payment.id),
                "payment_status": transition.old_status.value,
            },
            new_value={
                "payment_id": int(payment.id),
                "payment_status": transition.new_status.value,
                "decision": "refund_pending",
                "money_received_at": (
                    payment.paid_at.isoformat() if payment.paid_at else None
                ),
                "case_status_preserved": str(case.status),
            },
            comment=clean_comment,
        )
        await self.notifications.emit(
            event_code="SELF_FILING_PAYMENT_REFUND_PENDING",
            case_id=case.id,
            user_id=case.client_id,
            payload={
                "case_number": case.case_number,
                "payment_id": int(payment.id),
                "amount": str(payment.amount),
            },
            dedupe_key=f"payment:{payment.id}:self-filing-refund-pending",
        )
        await self.db.flush()
        return package, payment

    async def mark_package_ready(
        self,
        *,
        case_id: int,
        lawyer_id: int,
        document_id: int,
    ) -> SelfFilingPackage:
        case = await self._lock_case(case_id)
        if self._case_status(case) != CaseStatus.M1_SELF_FILING_PREPARATION:
            raise SelfFilingError("Пакет нельзя выдать на текущем этапе")
        package = await self.require_package(case_id=case.id, for_update=True)
        document = (
            await self.db.execute(
                select(Document)
                .where(
                    Document.id == int(document_id),
                    Document.case_id == int(case.id),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if document is None:
            raise SelfFilingError("Итоговый документ не найден")
        if (
            document.document_type != "SELF_FILING_PACKAGE"
            or document.status != DocumentStatus.APPROVED
            or not document_is_usable(document)
        ):
            raise SelfFilingError(
                "Для выдачи нужен проверенный и одобренный документ SELF_FILING_PACKAGE"
            )

        now = datetime.now(timezone.utc)
        package.package_document_id = int(document.id)
        package.ready_at = now
        package.status = SELF_FILING_STATUS_READY
        package.email_delivery_status = EMAIL_QUEUED
        package.email_last_error = None
        package.version = int(package.version or 1) + 1
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_SELF_FILING_READY,
            actor_type="lawyer",
            actor_id=int(lawyer_id),
            comment="Юрист утвердил итоговый пакет и поставил email-доставку в очередь",
        )
        await add_case_history_event(
            self.db,
            actor_type="lawyer",
            actor_id=int(lawyer_id),
            case_id=case.id,
            action="SELF_FILING_PACKAGE_READY",
            new_value={
                "package_id": int(package.id),
                "document_id": int(document.id),
                "document_sha256": document.sha256,
                "ready_at": now.isoformat(),
                "email_delivery_status": EMAIL_QUEUED,
            },
        )
        return package

    async def close_after_delivery(
        self,
        *,
        case_id: int,
        message_id: str | None,
        sent_at: datetime,
    ) -> SelfFilingPackage:
        case = await self._lock_case(case_id)
        package = await self.require_package(case_id=case.id, for_update=True)
        if self._case_status(case) not in {
            CaseStatus.M1_SELF_FILING_READY,
            CaseStatus.M1_SELF_FILING_DELIVERED,
        }:
            raise SelfFilingError("Email-доставка не соответствует текущему этапу")
        if package.email_delivery_status != EMAIL_SENT:
            raise SelfFilingError("Email ещё не подтверждён как отправленный")

        package.email_message_id = str(message_id or "").strip() or package.email_message_id
        package.email_sent_at = sent_at
        package.delivered_at = sent_at
        package.status = SELF_FILING_STATUS_DELIVERED
        package.version = int(package.version or 1) + 1

        if self._case_status(case) == CaseStatus.M1_SELF_FILING_READY:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M1_SELF_FILING_DELIVERED,
                actor_type="system",
                actor_id=None,
                comment="Готовый пакет подтверждённо отправлен на согласованный email клиента",
            )
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_SELF_FILING_CLOSED,
            actor_type="system",
            actor_id=None,
            comment=(
                "Услуга подготовки пакета завершена после подтверждённой email-доставки. "
                "Клиент подаёт документы и участвует в деле самостоятельно."
            ),
        )
        package.status = SELF_FILING_STATUS_CLOSED
        await add_case_history_event(
            self.db,
            actor_type="system",
            actor_id=None,
            case_id=case.id,
            action="SELF_FILING_PACKAGE_DELIVERED",
            new_value={
                "package_id": int(package.id),
                "document_id": package.package_document_id,
                "email": package.delivery_email,
                "email_message_id": package.email_message_id,
                "delivered_at": sent_at.isoformat(),
                "representation_included": False,
            },
        )
        return package


__all__ = [
    "EMAIL_FAILED",
    "EMAIL_NOT_QUEUED",
    "EMAIL_QUEUED",
    "EMAIL_SENDING",
    "EMAIL_SENT",
    "JURISDICTION_BASES",
    "SELF_FILING_REQUIRED_TYPES",
    "SelfFilingEmailVerificationError",
    "SelfFilingError",
    "SelfFilingService",
]
