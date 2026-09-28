from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.domain.cases.consent_contract import (
    CONSENT_CALLBACK_TOKEN,
    CONSENT_TEXT,
    CONSENT_TEXT_SHA256,
    SELF_FILING_CONSENT_CALLBACK_TOKEN,
    SELF_FILING_CONSENT_TEXT,
    SELF_FILING_CONSENT_TEXT_SHA256,
    consent_contract_for_service_mode,
    resolve_consent_contract,
)
from app.domain.cases.case_transition_policy import (
    ERROR_RECOVERY_TARGETS,
    transition_allowed,
)
from app.domain.cases.self_filing_business_calendar import (
    BusinessCalendarError,
    BusinessCalendarSnapshot,
    add_business_days,
)
from app.domain.cases.self_filing_contract import (
    SELF_FILING_EMAIL_VERIFICATION_MAX_ATTEMPTS,
    SELF_FILING_EMAIL_VERIFICATION_PBKDF2_ROUNDS,
    SELF_FILING_EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS,
    SELF_FILING_EMAIL_VERIFICATION_TTL_MINUTES,
    SELF_FILING_PRICE_RUB,
    SELF_FILING_DELIVERY_CALENDAR_DAYS,
)
from app.domain.cases.self_filing_email_sender import (
    email_delivery_configuration_error,
    email_delivery_configured,
)
from app.domain.cases.self_filing_service import (
    JURISDICTION_BASES,
    SELF_FILING_REQUIRED_TYPES,
    _email_code_hash,
)
from app.domain.cases.service_modes import M1ServiceMode
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.main import create_app
from app.system.settings_defaults import DEFAULT_SETTINGS


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_self_filing_is_m1_service_mode_not_third_route():
    assert M1ServiceMode.SELF_FILING_PACKAGE.value == "SELF_FILING_PACKAGE"
    assert M1ServiceMode.FULL_REPRESENTATION.value == "FULL_REPRESENTATION"

    source = read("app/domain/cases/post_calculation_decision_service.py")
    case_model = read("app/models/case.py")
    assert 'CHOICE_SELF_FILING = "self_filing"' in source
    assert "case.service_mode" in source
    assert "M1ServiceMode.SELF_FILING_PACKAGE.value" in source
    assert 'route: Mapped[str | None] = mapped_column(String(10)' in case_model
    assert "service_mode: Mapped[str | None]" in case_model
    assert '"M3"' not in source




def test_consent_can_start_self_filing_but_error_recovery_cannot_fake_financial_delivery_states():
    assert transition_allowed(
        CaseStatus.CLIENT_DECISION,
        CaseStatus.M1_SELF_FILING_PROFILE_PENDING,
    )
    assert CaseStatus.M1_SELF_FILING_PROFILE_PENDING in ERROR_RECOVERY_TARGETS
    assert CaseStatus.M1_SELF_FILING_DOCUMENTS_PENDING in ERROR_RECOVERY_TARGETS
    assert CaseStatus.M1_SELF_FILING_PAYMENT_PENDING not in ERROR_RECOVERY_TARGETS
    assert CaseStatus.M1_SELF_FILING_PREPARATION not in ERROR_RECOVERY_TARGETS
    assert CaseStatus.M1_SELF_FILING_READY not in ERROR_RECOVERY_TARGETS
    assert CaseStatus.M1_SELF_FILING_DELIVERED not in ERROR_RECOVERY_TARGETS


def test_commercial_contract_is_15k_and_three_calendar_days():
    assert PaymentCode.M1_SELF_FILING_PACKAGE.value == "M1_SELF_FILING_PACKAGE"
    assert SELF_FILING_PRICE_RUB == Decimal("15000")
    assert SELF_FILING_DELIVERY_CALENDAR_DAYS == 3
    assert DEFAULT_SETTINGS["payments.m1_self_filing_package"]["value"] == 15000
    assert DEFAULT_SETTINGS["self_filing.delivery_calendar_days"]["value"] == 3
    assert DEFAULT_SETTINGS["payments.m1_self_filing_package"]["type"] == "money"
    assert DEFAULT_SETTINGS["self_filing.delivery_calendar_days"]["type"] == "integer"

    service = read("app/domain/cases/self_filing_service.py")
    assert "async def _require_commercial_contract" in service
    assert "configured_price != SELF_FILING_PRICE_RUB" in service
    assert "configured_days != SELF_FILING_SLA_BUSINESS_DAYS" in service
    approve = service.split("async def approve_for_payment", 1)[1].split(
        "async def start_preparation_after_payment", 1
    )[0]
    paid = service.split("async def start_preparation_after_payment", 1)[1].split(
        "async def resolve_received_payment_review", 1
    )[0]
    assert "await self._require_commercial_contract()" in approve
    assert "payment=payment" in paid


def test_self_filing_new_sales_are_dark_until_controlled_activation():
    config = read("app/config.py")
    env_prod = read(".env.production.example")
    decision = read("app/domain/cases/post_calculation_decision_service.py")
    post = read("app/bot/screens/post_calculation.py")
    calculator = read("app/bot/screens/calculator.py")
    preview = read("app/bot/screens/calculator_preview.py")
    readiness = read("app/domain/cases/self_filing_readiness.py")

    assert "self_filing_new_sales_enabled: bool = False" in config
    assert "SELF_FILING_NEW_SALES_ENABLED=false" in env_prod
    assert "not bool(settings.self_filing_new_sales_enabled)" in decision
    assert "Пакет для самостоятельной подачи пока не открыт" in decision
    assert "if bool(settings.self_filing_new_sales_enabled):" in post
    assert "if bool(settings.self_filing_new_sales_enabled):" in calculator
    assert "if bool(settings.self_filing_new_sales_enabled):" in preview
    assert '"new_sales_enabled": new_sales_enabled' in readiness


def test_mailbox_verification_contract_is_fail_closed_and_never_stores_plaintext_code():
    assert SELF_FILING_EMAIL_VERIFICATION_TTL_MINUTES == 15
    assert SELF_FILING_EMAIL_VERIFICATION_MAX_ATTEMPTS == 5
    assert SELF_FILING_EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS == 60
    assert SELF_FILING_EMAIL_VERIFICATION_PBKDF2_ROUNDS == 120_000

    first = _email_code_hash(
        code="123456",
        salt_hex="00112233445566778899aabbccddeeff",
    )
    second = _email_code_hash(
        code="123456",
        salt_hex="ffeeddccbbaa99887766554433221100",
    )
    assert len(first) == 64
    assert first != second
    assert first != "123456"

    model = read("app/models/self_filing_package.py")
    migration = read("migrations/versions/20260925_0026_self_filing_package.py") + read(
        "migrations/versions/20260928_0027_self_filing_customer_contract.py"
    )
    service = read("app/domain/cases/self_filing_service.py")
    sender = read("app/domain/cases/self_filing_email_sender.py")

    for field in (
        "email_verification_salt",
        "email_verification_hash",
        "email_verification_expires_at",
        "email_verification_attempts",
        "email_verification_sent_at",
        "email_verification_message_id",
    ):
        assert field in model
        assert field in migration

    assert "email_verification_code" not in model
    assert "email_verification_code" not in migration
    assert "hashlib.pbkdf2_hmac" in service
    assert "hmac.compare_digest" in service
    assert "secrets.randbelow" in service
    assert "SELF_FILING_EMAIL_VERIFICATION_MAX_ATTEMPTS" in service
    assert "SELF_FILING_EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS" in service
    assert "SELF_FILING_EMAIL_VERIFICATION_FAILED" in service
    assert "SELF_FILING_EMAIL_VERIFICATION_EXPIRED" in service
    assert "send_self_filing_email_verification" in sender
    assert "Код подтверждения email" in sender


def test_profile_requires_mailbox_code_before_documents_or_payment():
    service = read("app/domain/cases/self_filing_service.py")
    bot = read("app/bot/screens/self_filing.py")
    states = read("app/bot/states.py")

    save_profile = service.split("async def save_confirmed_profile", 1)[1].split(
        "async def submit_documents", 1
    )[0]
    verify = service.split("async def verify_delivery_email", 1)[1].split(
        "@staticmethod", 1
    )[0]
    approve = service.split("async def approve_for_payment", 1)[1].split(
        "async def start_preparation_after_payment", 1
    )[0]

    assert "package.email_confirmed_at = None" in save_profile
    assert "SELF_FILING_PROFILE_SAVED_PENDING_EMAIL_VERIFICATION" in save_profile
    assert "return await self._issue_email_verification" in save_profile
    assert "M1_SELF_FILING_DOCUMENTS_PENDING" not in save_profile

    assert "package.email_confirmed_at = now" in verify
    assert "CaseStatus.M1_SELF_FILING_DOCUMENTS_PENDING" in verify
    assert "SELF_FILING_EMAIL_VERIFIED" in verify
    assert "email_confirmed_at" in approve

    assert "waiting_email_code = State()" in states
    assert "SelfFilingEmailVerificationError" in bot
    assert "self_filing_email_resend:v2:" in bot
    assert "self_filing_profile_restart:v2:" in bot
    assert "if error.persist_state:" in bot
    assert "await db.commit()" in bot
    assert "готовый пакет" in bot.lower()
    assert "только на подтверждённый адрес" in bot.lower()


def test_my_case_projects_pending_mailbox_verification_as_primary_client_step():
    view = read("app/bot/client_case_view.py")

    assert "def _self_filing_profile_projection" in view
    assert '"Нужно подтвердить email"' in view
    assert "До подтверждения email загрузка документов и оплата пакета" in view
    assert 'f"self_filing_profile_start:v2:{int(case.id)}"' in view
    assert "SelfFilingPackage.case_id == int(case.id)" in view
    assert "self_filing_package=self_filing_package" in view


def test_profile_confirmation_reuses_active_challenge_on_double_tap():
    service = read("app/domain/cases/self_filing_service.py")
    profile = service.split("async def save_confirmed_profile", 1)[1].split(
        "async def submit_documents", 1
    )[0]

    assert "package row lock serializes them" in profile
    assert 'str(package.client_region or "") == clean_region' in profile
    assert 'str(package.delivery_email or "").lower() == clean_email' in profile
    assert "bool(package.email_verification_hash)" in profile
    assert "package.email_verification_sent_at is not None" in profile
    assert "bool(package.email_verification_message_id)" in profile
    assert "datetime.now(timezone.utc) <= active_expires" in profile
    assert "return package" in profile


def test_documents_are_blocked_until_delivery_mailbox_is_verified():
    documents = read("app/bot/screens/documents.py")

    assert "CaseStatus.M1_SELF_FILING_PROFILE_PENDING" in documents
    assert "Сначала подтвердите email одноразовым кодом" in documents
    assert 'f"self_filing_profile_start:v2:{int(case.id)}"' in documents
    assert "_self_filing_upload_open(case)" in documents
    assert "Документы пока не принимаются." in documents


def test_staff_surface_exposes_verification_state_but_not_verification_secret():
    surface = read("app/api/self_filing_product.py")

    assert '"email_verified": package.email_confirmed_at is not None' in surface
    assert '"email_verification_pending": bool(' in surface
    assert '"email_verification_expires_at": (' in surface
    assert '"email_verification_attempts": int(' in surface
    assert "Email подтверждён" in surface
    assert "Адрес подтверждён" in surface
    assert '"email_verification_hash"' not in surface
    assert '"email_verification_salt"' not in surface


def test_client_minimum_documents_and_jurisdiction_are_explicit():
    assert SELF_FILING_REQUIRED_TYPES == frozenset({"DDU", "PASSPORT"})
    assert "CLIENT_RESIDENCE_OR_STAY" in JURISDICTION_BASES
    assert "DEFENDANT_LOCATION" in JURISDICTION_BASES
    assert "CONTRACT_CONCLUSION_OR_PERFORMANCE" in JURISDICTION_BASES
    assert "OTHER_LAWYER_CONFIRMED" in JURISDICTION_BASES

    source = read("app/domain/cases/self_filing_service.py")
    assert "court_auto_selected" in source
    assert "jurisdiction_confirmed_by_lawyer_id" in source
    assert "documents_complete_by_lawyer_id" in source
    assert "не приняты обязательные документы" in source
    assert "без итогового статуса APPROVED" in source


def _calendar(
    *,
    coverage: str,
    non_working: tuple[str, ...] = (),
    additional_working: tuple[str, ...] = (),
) -> BusinessCalendarSnapshot:
    return BusinessCalendarSnapshot(
        timezone="Europe/Moscow",
        coverage_through=date.fromisoformat(coverage),
        non_working_dates=frozenset(date.fromisoformat(x) for x in non_working),
        additional_working_dates=frozenset(
            date.fromisoformat(x) for x in additional_working
        ),
    )


def test_two_business_days_skip_weekend_and_preserve_business_wall_clock():
    msk = ZoneInfo("Europe/Moscow")
    friday = datetime(2026, 9, 25, 15, 30, tzinfo=msk)
    due = add_business_days(
        friday,
        business_days=2,
        calendar=_calendar(coverage="2026-10-31"),
    )
    assert due == datetime(2026, 9, 29, 15, 30, tzinfo=msk)


def test_business_calendar_respects_declared_holiday_and_working_weekend():
    msk = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 9, 25, 10, 0, tzinfo=msk)
    due = add_business_days(
        start,
        business_days=2,
        calendar=_calendar(
            coverage="2026-10-31",
            non_working=("2026-09-28",),
            additional_working=("2026-09-27",),
        ),
    )
    # Sunday 27th is explicitly working and Monday 28th explicitly non-working.
    assert due == datetime(2026, 9, 29, 10, 0, tzinfo=msk)


def test_business_calendar_fails_closed_outside_confirmed_coverage():
    msk = ZoneInfo("Europe/Moscow")
    with pytest.raises(BusinessCalendarError, match="не покрывает"):
        add_business_days(
            datetime(2026, 9, 25, 10, 0, tzinfo=msk),
            business_days=2,
            calendar=_calendar(coverage="2026-09-25"),
        )


def test_self_filing_consent_is_separate_and_original_pd1_is_stable():
    full = resolve_consent_contract(CONSENT_CALLBACK_TOKEN)
    package = resolve_consent_contract(SELF_FILING_CONSENT_CALLBACK_TOKEN)

    assert CONSENT_CALLBACK_TOKEN == "pd1"
    assert SELF_FILING_CONSENT_CALLBACK_TOKEN == "pd2"
    assert full is not None and package is not None
    assert full["text"] == CONSENT_TEXT
    assert full["text_sha256"] == CONSENT_TEXT_SHA256
    assert package["text"] == SELF_FILING_CONSENT_TEXT
    assert package["text_sha256"] == SELF_FILING_CONSENT_TEXT_SHA256
    assert package["text_sha256"] != full["text_sha256"]
    assert package["service_mode"] == M1ServiceMode.SELF_FILING_PACKAGE.value
    assert (
        consent_contract_for_service_mode(M1ServiceMode.SELF_FILING_PACKAGE.value)[
            "callback_token"
        ]
        == "pd2"
    )
    assert (
        consent_contract_for_service_mode(M1ServiceMode.FULL_REPRESENTATION.value)[
            "callback_token"
        ]
        == "pd1"
    )


def _configure_valid_smtp(monkeypatch):
    monkeypatch.setattr(settings, "self_filing_email_provider", "smtp")
    monkeypatch.setattr(settings, "self_filing_smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "self_filing_smtp_port", 587)
    monkeypatch.setattr(
        settings, "self_filing_smtp_from_email", "law@example.test"
    )
    monkeypatch.setattr(settings, "self_filing_smtp_username", "user")
    monkeypatch.setattr(settings, "self_filing_smtp_password", "secret")
    monkeypatch.setattr(settings, "self_filing_email_max_attempts", 8)
    monkeypatch.setattr(settings, "self_filing_email_timeout_seconds", 30)


def test_email_delivery_is_fail_closed_until_complete_provider_contract(monkeypatch):
    _configure_valid_smtp(monkeypatch)
    assert email_delivery_configuration_error() is None
    assert email_delivery_configured() is True

    monkeypatch.setattr(settings, "self_filing_smtp_password", "")
    assert "USERNAME" in str(email_delivery_configuration_error())
    assert email_delivery_configured() is False

    monkeypatch.setattr(settings, "self_filing_smtp_password", "secret")
    monkeypatch.setattr(settings, "self_filing_smtp_port", 0)
    assert "PORT" in str(email_delivery_configuration_error())
    assert email_delivery_configured() is False


def test_claim_calculation_cutoff_follows_customer_rule():
    source = read("app/domain/cases/self_filing_service.py")
    freeze = source.split("async def _freeze_claim_calculation", 1)[1].split(
        "async def _issue_email_verification", 1
    )[0]
    assert 'basis = "TRANSFER_ACT_DATE"' in freeze
    assert 'basis = "SERVICE_PAYMENT_DATE"' in freeze
    assert "cutoff = source.actual_transfer_date" in freeze
    assert "cutoff = local_paid_at.date()" in freeze
    assert "update_in_court = True" in freeze
    assert "is_preliminary=False" in freeze


def test_payment_and_sla_only_start_after_lawyer_completeness_gate():
    source = read("app/domain/cases/self_filing_service.py")
    approve = source.split("async def approve_for_payment", 1)[1].split(
        "async def start_preparation_after_payment", 1
    )[0]
    paid = source.split("async def start_preparation_after_payment", 1)[1].split(
        "async def mark_package_ready", 1
    )[0]

    assert "_approved_document_gate" in approve
    assert "jurisdiction_confirmed_at" in approve
    assert "require_email_delivery_configured" in approve
    assert "_require_customer_payment_provider" in approve
    assert "PaymentCode.M1_SELF_FILING_PACKAGE" in approve

    assert "payment.status" in paid
    assert "documents_complete_at" in paid
    assert "jurisdiction_confirmed_at" in paid
    assert "package.sla_started_at = payment_at" in paid
    assert "payment_at + timedelta(days=calendar_days)" in paid
    assert "_freeze_claim_calculation" in paid
    assert "M1_SELF_FILING_PREPARATION" in paid


def test_final_package_is_exact_four_approved_documents_and_delivery_closes_only_after_send():
    docs = read("app/domain/cases/self_filing_documents.py")
    service = read("app/domain/cases/self_filing_service.py")
    sender = read("app/domain/cases/self_filing_email_sender.py")

    for document_type in (
        "SELF_FILING_PRETRIAL_CLAIM",
        "SELF_FILING_STATEMENT_OF_CLAIM",
        "SELF_FILING_CLAIM_CALCULATION",
        "SELF_FILING_CLIENT_ROADMAP",
    ):
        assert document_type in docs
    assert "document.status = DocumentStatus.APPROVED" in docs
    assert '"sha256": document.sha256' in docs

    ready = service.split("async def mark_package_ready", 1)[1].split(
        "async def close_after_delivery", 1
    )[0]
    assert "SELF_FILING_DELIVERABLE_FIELDS" in ready
    assert "document.status != DocumentStatus.APPROVED" in ready
    assert "all(deliverable_ids.values())" in ready
    assert "package.email_delivery_status = EMAIL_QUEUED" in ready

    assert "read_document_bytes" in sender
    assert "expected_sha256=document.sha256" in sender
    assert "package.email_delivery_status = EMAIL_SENT" in sender
    assert "close_after_delivery" in sender
    assert "SMTP itself cannot provide transactional exactly-once semantics" in sender


def test_telegram_and_client_projection_expose_exact_self_filing_path():
    post = read("app/bot/screens/post_calculation.py")
    view = read("app/bot/client_case_view.py")
    payments = read("app/bot/screens/payments.py")
    scope = read("app/bot/case_callback_scope.py")

    assert "calc_self_filing:v2:" in post
    assert "15 000 ₽" in post
    assert "3 календарных дней" in post
    assert "Услуга доступна клиентам по России" in post
    assert '"M1_SELF_FILING_PAYMENT_PENDING": ClientAction(' in view
    assert '"pay_self_filing"' in view
    assert "self_filing_profile_start:v2:" in view
    assert '"pay_self_filing"' in scope
    assert "PaymentCode.M1_SELF_FILING_PACKAGE" in payments


def test_self_filing_staff_surface_uses_business_timezone_and_human_stage_copy():
    surface = read("app/api/self_filing_product.py")

    assert '"business_timezone": settings.business_timezone' in surface
    assert '"business_timezone_label": settings.business_timezone_label' in surface
    assert "SELF_FILING_STATUS_LABELS" in surface
    assert "status_label" in surface
    assert "Главный следующий шаг" in surface
    assert "Intl.DateTimeFormat('ru-RU'" in surface
    assert "timeZone:zone" in surface
    assert "new Date(s).toLocaleString('ru-RU')" not in surface


def test_staff_surface_and_workdesk_are_registered_and_case_bound():
    app = create_app()
    routes = {
        (route.path, method)
        for route in app.routes
        if hasattr(route, "path") and hasattr(route, "methods")
        for method in (route.methods or set())
    }
    assert ("/self-filing/cases/{case_id}", "GET") in routes
    assert ("/self-filing/cases/{case_id}/review/start", "POST") in routes
    assert ("/self-filing/cases/{case_id}/request-documents", "POST") in routes
    assert ("/self-filing/cases/{case_id}/approve-for-payment", "POST") in routes
    assert (
        "/self-filing/cases/{case_id}/payment-review/{payment_id}/resolve",
        "POST",
    ) in routes
    assert ("/self-filing/cases/{case_id}/package", "POST") in routes
    assert ("/self-filing/cases/{case_id}/email/retry", "POST") in routes
    assert ("/self-filing/ui", "GET") in routes

    surface = read("app/api/self_filing_product.py")
    workdesk = read("app/api/workdesk.py")
    lawyer = read("app/api/lawyer_product.py")
    assert "expected_package_version" in surface
    assert "lawyer_can_access_case" in surface
    assert "SELF_FILING_PACKAGE" in surface
    assert 'href": f"/self-filing/ui?case_id={case.id}"' in workdesk
    assert "M1_SELF_FILING_PREPARATION" in lawyer
    assert "SELF_FILING_OVERDUE" in lawyer


def test_terminal_and_client_visible_statuses_cover_self_filing_service():
    expected = {
        CaseStatus.M1_SELF_FILING_PROFILE_PENDING,
        CaseStatus.M1_SELF_FILING_DOCUMENTS_PENDING,
        CaseStatus.M1_SELF_FILING_DOCUMENTS_RECEIVED,
        CaseStatus.M1_SELF_FILING_LAWYER_REVIEW,
        CaseStatus.M1_SELF_FILING_DOCS_REQUESTED,
        CaseStatus.M1_SELF_FILING_PAYMENT_PENDING,
        CaseStatus.M1_SELF_FILING_PREPARATION,
        CaseStatus.M1_SELF_FILING_READY,
        CaseStatus.M1_SELF_FILING_DELIVERED,
        CaseStatus.M1_SELF_FILING_CLOSED,
    }
    timeline = read("app/domain/cases/case_timeline.py")
    for status in expected:
        assert f"CaseStatus.{status.name}" in timeline
    assert '"M1_SELF_FILING_CLOSED": "M1_SELF_FILING_COMPLETED"' in read(
        "app/models/case.py"
    )


def test_no_moscow_only_gate_is_introduced_for_self_filing_scope():
    intake = read("app/bot/screens/self_filing.py")
    service = read("app/domain/cases/self_filing_service.py")

    assert "регион России" in intake.lower()
    assert "client_region" in service
    assert "Moscow" not in service
    assert "Moscow" not in intake
