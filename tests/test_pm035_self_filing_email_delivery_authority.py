from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.domain.cases.self_filing_email_sender as sender_module
from app.config import settings
from app.domain.cases.self_filing_documents import SELF_FILING_DELIVERABLE_FIELDS
from app.domain.cases.self_filing_email_sender import (
    ATTEMPT_FAILED,
    ATTEMPT_SENT,
    ATTEMPT_SENDING,
    ATTEMPT_UNKNOWN,
    SelfFilingEmailDeliveryUnknownError,
    SelfFilingEmailSender,
    SelfFilingSMTPDefinitiveFailure,
)
from app.domain.cases.self_filing_service import (
    EMAIL_FAILED,
    EMAIL_QUEUED,
    EMAIL_SENT,
    EMAIL_SENDING,
    EMAIL_UNKNOWN,
    SelfFilingService,
)
from app.models import Base
from app.models.case import Case
from app.models.document import Document
from app.models.self_filing_email_delivery_attempt import SelfFilingEmailDeliveryAttempt
from app.models.self_filing_package import SelfFilingPackage
from app.models.user import User


@asynccontextmanager
async def database(tmp_path, name: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield factory
    finally:
        await engine.dispose()


def configure_smtp(monkeypatch) -> None:
    monkeypatch.setattr(settings, "self_filing_email_provider", "smtp")
    monkeypatch.setattr(settings, "self_filing_smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "self_filing_smtp_port", 587)
    monkeypatch.setattr(settings, "self_filing_smtp_username", "test-user")
    monkeypatch.setattr(settings, "self_filing_smtp_password", "test-password")
    monkeypatch.setattr(settings, "self_filing_smtp_from_email", "legal@example.test")
    monkeypatch.setattr(settings, "self_filing_smtp_starttls", True)
    monkeypatch.setattr(settings, "self_filing_email_max_attempts", 8)
    monkeypatch.setattr(settings, "self_filing_email_timeout_seconds", 30)


async def seed_ready_package(session, *, suffix: int) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    user = User(
        telegram_id=9_970_000 + suffix,
        full_name=f"Delivery authority client {suffix}",
    )
    session.add(user)
    await session.flush()

    case = Case(
        case_number=f"SELF-FILING-DELIVERY-{suffix}",
        client_id=int(user.id),
        route="M1",
        service_mode="SELF_FILING_PACKAGE",
        status="M1_SELF_FILING_READY",
        title="Ready self-filing package",
        next_action="Ожидать доставку готового пакета",
    )
    session.add(case)
    await session.flush()

    package = SelfFilingPackage(
        case_id=int(case.id),
        status="READY",
        version=11,
        delivery_email="client@example.test",
        email_confirmed_at=now - timedelta(hours=1),
        ready_at=now - timedelta(minutes=5),
        email_delivery_status=EMAIL_QUEUED,
        email_delivery_attempts=0,
        court_name="Тестовый районный суд",
        court_address="Тестовый адрес суда",
        claim_update_in_court_required=False,
    )
    session.add(package)
    await session.flush()

    documents: dict[str, Document] = {}
    for index, dtype in enumerate(SELF_FILING_DELIVERABLE_FIELDS, start=1):
        document = Document(
            case_id=int(case.id),
            uploaded_by_user_id=int(user.id),
            document_type=dtype,
            title=f"Deliverable {index}",
            file_name=f"deliverable-{index}.pdf",
            file_path=f"cases/{int(case.id)}/deliverable-{index}.dlcenc",
            mime_type="application/pdf",
            file_size=100 + index,
            sha256=(f"{index:02x}" * 32)[:64],
            security_status="VERIFIED",
            encryption_status="ENCRYPTED",
            encryption_format_version=2,
            encryption_key_id="test-key",
            encryption_envelope_id=f"env-{suffix}-{index}",
            encrypted_data_key="ciphertext",
            encrypted_data_key_nonce="00" * 12,
            version=1,
            status="APPROVED",
        )
        session.add(document)
        await session.flush()
        documents[dtype] = document

    for dtype, field in SELF_FILING_DELIVERABLE_FIELDS.items():
        setattr(package, field, int(documents[dtype].id))

    await session.commit()
    return int(case.id), int(package.id)


async def patch_sender_dependencies(monkeypatch, sender: SelfFilingEmailSender) -> None:
    async def fake_compose(*, message_id: str, **_kwargs):
        message = EmailMessage()
        message["Message-ID"] = message_id
        message.set_content("test")
        return message

    async def fake_history(*_args, **_kwargs):
        return None

    async def fake_emit(*_args, **_kwargs):
        return True

    async def fake_close(*_args, **_kwargs):
        return None

    monkeypatch.setattr(sender, "_compose", fake_compose)
    monkeypatch.setattr(sender_module, "add_case_history_event", fake_history)
    monkeypatch.setattr(sender.notifications, "emit", fake_emit)
    monkeypatch.setattr(SelfFilingService, "close_after_delivery", fake_close)


@pytest.mark.asyncio
async def test_sent_attempt_is_durable_and_exact_retry_does_not_call_smtp_twice(
    tmp_path,
    monkeypatch,
):
    configure_smtp(monkeypatch)
    calls: list[str] = []

    def smtp_send(message):
        calls.append(str(message["Message-ID"]))

    monkeypatch.setattr(sender_module, "_smtp_send", smtp_send)

    async with database(tmp_path, "pm035-sent.db") as factory:
        async with factory() as session:
            _case_id, package_id = await seed_ready_package(session, suffix=1)
            sender = SelfFilingEmailSender(session)
            await patch_sender_dependencies(monkeypatch, sender)

            assert await sender.send_one(package_id) is True
            assert await sender.send_one(package_id) is True
            assert len(calls) == 1

            package = await session.get(SelfFilingPackage, package_id)
            attempt = (
                await session.execute(
                    select(SelfFilingEmailDeliveryAttempt).where(
                        SelfFilingEmailDeliveryAttempt.package_id == package_id
                    )
                )
            ).scalar_one()
            assert package is not None
            assert package.email_delivery_status == EMAIL_SENT
            assert attempt.state == ATTEMPT_SENT
            assert attempt.sent_at is not None
            assert attempt.message_id == package.email_message_id
            assert len(attempt.documents_snapshot) == 4
            assert {item["type"] for item in attempt.documents_snapshot} == set(
                SELF_FILING_DELIVERABLE_FIELDS
            )
            assert all(len(str(item["sha256"])) == 64 for item in attempt.documents_snapshot)


@pytest.mark.asyncio
async def test_explicit_smtp_rejection_is_failed_and_retryable(
    tmp_path,
    monkeypatch,
):
    configure_smtp(monkeypatch)

    def rejected(_message):
        raise SelfFilingSMTPDefinitiveFailure("550 rejected")

    monkeypatch.setattr(sender_module, "_smtp_send", rejected)

    async with database(tmp_path, "pm035-failed.db") as factory:
        async with factory() as session:
            _case_id, package_id = await seed_ready_package(session, suffix=2)
            sender = SelfFilingEmailSender(session)
            await patch_sender_dependencies(monkeypatch, sender)

            assert await sender.send_one(package_id) is False
            package = await session.get(SelfFilingPackage, package_id)
            attempt = (
                await session.execute(
                    select(SelfFilingEmailDeliveryAttempt).where(
                        SelfFilingEmailDeliveryAttempt.package_id == package_id
                    )
                )
            ).scalar_one()
            assert package is not None
            assert package.email_delivery_status == EMAIL_FAILED
            assert attempt.state == ATTEMPT_FAILED
            assert attempt.failed_at is not None


@pytest.mark.asyncio
async def test_smtp_success_then_db_failure_becomes_unknown_and_is_not_auto_retried(
    tmp_path,
    monkeypatch,
):
    configure_smtp(monkeypatch)
    smtp_calls: list[str] = []

    def smtp_send(message):
        smtp_calls.append(str(message["Message-ID"]))

    monkeypatch.setattr(sender_module, "_smtp_send", smtp_send)

    async with database(tmp_path, "pm035-unknown.db") as factory:
        async with factory() as session:
            _case_id, package_id = await seed_ready_package(session, suffix=3)
            sender = SelfFilingEmailSender(session)
            await patch_sender_dependencies(monkeypatch, sender)

            real_commit = session.commit
            commit_count = 0

            async def fail_post_send_commit():
                nonlocal commit_count
                commit_count += 1
                # PREPARED and SENDING commits are durable. Simulate a DB failure
                # exactly when SENT/business-close evidence would be committed.
                if commit_count == 3:
                    raise RuntimeError("simulated post-SMTP commit failure")
                await real_commit()

            monkeypatch.setattr(session, "commit", fail_post_send_commit)

            with pytest.raises(SelfFilingEmailDeliveryUnknownError):
                await sender.send_one(package_id)

            package = await session.get(SelfFilingPackage, package_id)
            attempt = (
                await session.execute(
                    select(SelfFilingEmailDeliveryAttempt).where(
                        SelfFilingEmailDeliveryAttempt.package_id == package_id
                    )
                )
            ).scalar_one()
            assert package is not None
            assert package.email_delivery_status == EMAIL_UNKNOWN
            assert attempt.state == ATTEMPT_UNKNOWN
            assert attempt.unknown_at is not None
            assert len(smtp_calls) == 1

            result = await sender.send_pending()
            assert result["sent"] == 0
            assert len(smtp_calls) == 1


@pytest.mark.asyncio
async def test_stale_sending_is_reconciled_to_unknown_not_failed(
    tmp_path,
    monkeypatch,
):
    configure_smtp(monkeypatch)

    async with database(tmp_path, "pm035-stale.db") as factory:
        async with factory() as session:
            case_id, package_id = await seed_ready_package(session, suffix=4)
            package = await session.get(SelfFilingPackage, package_id)
            assert package is not None
            package.email_delivery_status = EMAIL_SENDING
            package.email_delivery_attempts = 1
            package.email_message_id = "<stale@example.test>"
            attempt = SelfFilingEmailDeliveryAttempt(
                package_id=package_id,
                case_id=case_id,
                package_version=int(package.version),
                attempt_number=1,
                recipient_email="client@example.test",
                message_id=package.email_message_id,
                state=ATTEMPT_SENDING,
                documents_snapshot=[],
                prepared_at=datetime.now(timezone.utc) - timedelta(minutes=10),
                sending_at=datetime.now(timezone.utc) - timedelta(minutes=10),
            )
            session.add(attempt)
            await session.commit()

            sender = SelfFilingEmailSender(session)
            await patch_sender_dependencies(monkeypatch, sender)
            assert await sender.reconcile_stale_sending() == 1

            await session.refresh(package)
            await session.refresh(attempt)
            assert package.email_delivery_status == EMAIL_UNKNOWN
            assert attempt.state == ATTEMPT_UNKNOWN
            assert attempt.unknown_at is not None


def test_source_commits_prepared_and_sending_before_external_smtp() -> None:
    import inspect

    source = inspect.getsource(SelfFilingEmailSender)
    prepared = source.index("PREPARED is durable before")
    prepared_commit = source.index("await self.db.commit()", prepared)
    sending = source.index("durable external-side-effect fence")
    sending_commit = source.index("await self.db.commit()", sending)
    smtp = source.index("await asyncio.to_thread(_smtp_send, message)")

    assert prepared < prepared_commit < sending < sending_commit < smtp
    assert "ATTEMPT_UNKNOWN" in source
    assert "EMAIL_UNKNOWN" in source
    assert "unknown_reconciled" in source
