from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.domain.retention.case_retention_service import (
    CaseRetentionError,
    CaseRetentionService,
    STATUS_APPROVED,
    STATUS_COMPLETED,
    STATUS_DISCOVERED,
    STATUS_EXECUTING,
    STATUS_FAILED,
    STATUS_REQUESTED,
)
from app.models import Base
from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog
from app.models.calculation import Calculation
from app.models.case import Case
from app.models.case_retention import CaseRetentionRecord
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.document_access_grant import DocumentAccessGrant
from app.models.message import Message
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.user import User
from app.security.audit_integrity import verify_audit_chain


async def create_database(tmp_path, name: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, factory


def configure_retention(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "closed_case_retention_days", 30)
    monkeypatch.setattr(settings, "case_retention_scan_batch_size", 100)
    monkeypatch.setattr(settings, "case_retention_execution_timeout_seconds", 900)
    monkeypatch.setattr(settings, "case_retention_dry_run", True)
    monkeypatch.setattr(settings, "audit_integrity_key_id", "audit-retention-test")
    monkeypatch.setattr(
        settings,
        "audit_integrity_key",
        "audit-retention-test-key-" + "x" * 40,
    )
    monkeypatch.setattr(settings, "audit_integrity_previous_keys", "")


async def seed_closed_case(db, tmp_path, *, suffix: int = 1, payment_status: str = "PAID"):
    now = datetime.now(timezone.utc)
    user = User(
        telegram_id=800000 + suffix,
        full_name=f"Клиент retention {suffix}",
        email=f"client-{suffix}@example.test",
    )
    requester = AdminUser(
        full_name=f"Запрашивающий {suffix}",
        username=f"retention-requester-{suffix}",
        email=f"retention-requester-{suffix}@example.test",
        password_hash="unused",
        role="superadmin,admin",
        is_active=True,
        mfa_enabled=True,
        session_version=1,
    )
    approver = AdminUser(
        full_name=f"Одобряющий {suffix}",
        username=f"retention-approver-{suffix}",
        email=f"retention-approver-{suffix}@example.test",
        password_hash="unused",
        role="superadmin,admin",
        is_active=True,
        mfa_enabled=True,
        session_version=1,
    )
    db.add_all([user, requester, approver])
    await db.flush()

    case = Case(
        case_number=f"RET-{suffix:04d}",
        client_id=user.id,
        route="M1",
        status="M1_CLOSED",
        title="Закрытое дело с персональными данными",
        internal_comment="Удаляемый внутренний комментарий",
        closed_at=now - timedelta(days=31),
        next_action="Дело завершено",
        sla_status="CLOSED",
    )
    db.add(case)
    await db.flush()

    payment = Payment(
        case_id=case.id,
        payment_code="M1_INITIAL_PAYMENT",
        title="Сохранённый финансовый документ",
        amount=Decimal("30000.00"),
        currency="RUB",
        status=payment_status,
        provider="fake",
        provider_payment_id=f"provider-{suffix}",
    )
    consultation = Consultation(
        case_id=case.id,
        status="DONE",
        consultation_type="online",
        subject_type="existing_case",
        scheduled_at=now - timedelta(days=40),
        client_description="Удаляемое описание клиента",
        lawyer_result="Удаляемое заключение юриста",
        decision="CLOSE",
    )
    message = Message(
        case_id=case.id,
        sender_type="client",
        sender_id=user.id,
        text="Удаляемое сообщение",
    )
    notification = Notification(
        case_id=case.id,
        user_id=user.id,
        event_code="RETENTION_TEST",
        text="Удаляемое уведомление",
        status="PENDING",
    )
    calculation = Calculation(
        case_id=case.id,
        contract_price=Decimal("8500000.00"),
        delay_days=10,
        penalty_amount=Decimal("100000.00"),
        formula_version="ddu-consumer-v1",
    )
    db.add_all([payment, consultation, message, notification, calculation])
    await db.flush()

    storage_path = (
        tmp_path
        / "storage"
        / "cases"
        / str(case.id)
        / f"{suffix:032x}.dlcenc"
    )
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path.write_bytes(b"encrypted-content")
    document = Document(
        case_id=case.id,
        uploaded_by_user_id=user.id,
        document_type="DDU",
        title="ДДУ",
        file_name="contract.pdf",
        file_path=str(storage_path),
        mime_type="application/pdf",
        file_size=17,
        sha256=("a" * 63) + str(suffix % 10),
        detected_type="pdf",
        security_status="VERIFIED",
        scanned_at=now,
        encryption_status="ENCRYPTED",
        encryption_key_id="documents-test",
        encrypted_at=now,
        status="ON_REVIEW",
    )
    db.add(document)
    await db.flush()

    grant = DocumentAccessGrant(
        public_id=f"grant{suffix:027d}"[-32:],
        document_id=document.id,
        case_id=case.id,
        actor_account_id=requester.id,
        actor_role="superadmin",
        session_jti_ref="j" * 64,
        session_version=1,
        token_key_id="hmac-test",
        token_digest=("b" * 63) + str(suffix % 10),
        expires_at=now + timedelta(minutes=2),
    )
    db.add(grant)
    await db.flush()
    return {
        "case": case,
        "user": user,
        "requester": requester,
        "approver": approver,
        "payment": payment,
        "consultation": consultation,
        "document": document,
        "path": storage_path,
    }


@pytest.mark.asyncio
async def test_four_eyes_execution_deletes_content_but_preserves_ledger_and_audit(
    tmp_path,
    monkeypatch,
):
    configure_retention(monkeypatch, tmp_path)
    engine, factory = await create_database(tmp_path, "retention-execute.db")
    async with factory() as db:
        context = await seed_closed_case(db, tmp_path, suffix=1)
        await db.commit()
        service = CaseRetentionService(db)
        record = await service.request_deletion(
            case_id=context["case"].id,
            actor_id=context["requester"].id,
            reason="Истёк утверждённый срок хранения закрытого дела",
        )
        assert record.status == STATUS_REQUESTED
        with pytest.raises(CaseRetentionError, match="разные суперадминистраторы"):
            await service.approve_deletion(
                record_id=record.id,
                actor_id=context["requester"].id,
                comment="Самостоятельное одобрение не допускается",
            )

        record = await service.approve_deletion(
            record_id=record.id,
            actor_id=context["approver"].id,
            comment="Проверены закрытие дела, срок и отсутствие незавершённых операций",
        )
        assert record.status == STATUS_APPROVED
        await db.commit()

        record = await service.execute_deletion(
            record_id=record.id,
            actor_id=context["approver"].id,
        )
        assert record.status == STATUS_COMPLETED
        assert record.documents_deleted == 1
        assert record.messages_deleted == 1
        assert record.notifications_deleted == 1
        assert record.consultations_anonymized == 1
        assert record.content_digest and len(record.content_digest) == 64
        assert not context["path"].exists()

        assert (
            await db.execute(
                select(func.count(Document.id)).where(Document.case_id == context["case"].id)
            )
        ).scalar_one() == 0
        assert (
            await db.execute(
                select(func.count(Message.id)).where(Message.case_id == context["case"].id)
            )
        ).scalar_one() == 0
        assert (
            await db.execute(
                select(func.count(Notification.id)).where(
                    Notification.case_id == context["case"].id
                )
            )
        ).scalar_one() == 0
        assert (
            await db.execute(
                select(func.count(DocumentAccessGrant.id)).where(
                    DocumentAccessGrant.case_id == context["case"].id
                )
            )
        ).scalar_one() == 0
        assert (
            await db.execute(
                select(func.count(Calculation.id)).where(
                    Calculation.case_id == context["case"].id
                )
            )
        ).scalar_one() == 0
        assert (
            await db.execute(
                select(func.count(Payment.id)).where(Payment.case_id == context["case"].id)
            )
        ).scalar_one() == 1

        consultation = await db.get(Consultation, context["consultation"].id)
        assert consultation.status == "CONTENT_DELETED"
        assert consultation.client_description is None
        assert consultation.lawyer_result is None
        case = await db.get(Case, context["case"].id)
        assert case.content_deleted_at is not None
        assert case.internal_comment is None
        assert case.title == "Содержимое удалено по политике хранения"
        assert (
            await db.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_id == case.id,
                    AuditLog.action == "CASE_RETENTION_CONTENT_DELETED",
                )
            )
        ).scalar_one() == 1
        assert (await verify_audit_chain(db))["ok"] is True

        repeated = await service.execute_deletion(
            record_id=record.id,
            actor_id=context["approver"].id,
        )
        assert repeated.status == STATUS_COMPLETED

    await engine.dispose()


@pytest.mark.asyncio
async def test_legal_hold_blocks_and_invalidates_previous_approval(tmp_path, monkeypatch):
    configure_retention(monkeypatch, tmp_path)
    engine, factory = await create_database(tmp_path, "retention-hold.db")
    async with factory() as db:
        context = await seed_closed_case(db, tmp_path, suffix=2)
        service = CaseRetentionService(db)
        record = await service.request_deletion(
            case_id=context["case"].id,
            actor_id=context["requester"].id,
            reason="Истёк срок хранения и выполнена первичная проверка",
        )
        record = await service.approve_deletion(
            record_id=record.id,
            actor_id=context["approver"].id,
            comment="Подтверждена готовность к контролируемому удалению",
        )
        assert record.status == STATUS_APPROVED

        record = await service.set_legal_hold(
            case_id=context["case"].id,
            actor_id=context["approver"].id,
            reason="Получен новый судебный запрос, данные необходимо сохранить",
        )
        assert record.legal_hold is True
        assert record.status == STATUS_DISCOVERED
        assert record.requested_by is None
        assert record.approved_by is None
        with pytest.raises(CaseRetentionError, match="legal hold"):
            await service.request_deletion(
                case_id=context["case"].id,
                actor_id=context["requester"].id,
                reason="Попытка удаления при действующем hold",
            )

        record = await service.release_legal_hold(
            case_id=context["case"].id,
            actor_id=context["approver"].id,
            reason="Судебный запрос закрыт и обязательство хранения прекращено",
        )
        assert record.legal_hold is False
        record = await service.request_deletion(
            case_id=context["case"].id,
            actor_id=context["requester"].id,
            reason="Повторный запрос после снятия legal hold",
        )
        assert record.status == STATUS_REQUESTED

    await engine.dispose()


@pytest.mark.asyncio
async def test_unresolved_payment_blocks_request(tmp_path, monkeypatch):
    configure_retention(monkeypatch, tmp_path)
    engine, factory = await create_database(tmp_path, "retention-payment.db")
    async with factory() as db:
        context = await seed_closed_case(
            db,
            tmp_path,
            suffix=3,
            payment_status="REFUND_PENDING",
        )
        with pytest.raises(CaseRetentionError, match="платёжные статусы"):
            await CaseRetentionService(db).request_deletion(
                case_id=context["case"].id,
                actor_id=context["requester"].id,
                reason="Запрос не должен пройти до завершения возврата",
            )
        assert context["path"].exists()

    await engine.dispose()


@pytest.mark.asyncio
async def test_unsafe_path_fails_before_any_file_is_deleted(tmp_path, monkeypatch):
    configure_retention(monkeypatch, tmp_path)
    engine, factory = await create_database(tmp_path, "retention-path.db")
    async with factory() as db:
        context = await seed_closed_case(db, tmp_path, suffix=4)
        outside = tmp_path / "outside.dlcenc"
        outside.write_bytes(b"outside")
        unsafe = Document(
            case_id=context["case"].id,
            uploaded_by_user_id=context["user"].id,
            document_type="OTHER",
            title="Небезопасный путь",
            file_name="outside.pdf",
            file_path=str(outside),
            mime_type="application/pdf",
            file_size=7,
            sha256="c" * 64,
            detected_type="pdf",
            security_status="VERIFIED",
            scanned_at=datetime.now(timezone.utc),
            encryption_status="ENCRYPTED",
            encryption_key_id="documents-test",
            encrypted_at=datetime.now(timezone.utc),
            status="ON_REVIEW",
        )
        db.add(unsafe)
        await db.commit()
        service = CaseRetentionService(db)
        record = await service.request_deletion(
            case_id=context["case"].id,
            actor_id=context["requester"].id,
            reason="Подготовка теста границ защищённого хранилища",
        )
        record = await service.approve_deletion(
            record_id=record.id,
            actor_id=context["approver"].id,
            comment="Тестовый запрос одобрен вторым администратором",
        )
        await db.commit()
        with pytest.raises(CaseRetentionError, match="Небезопасный путь документа"):
            await service.execute_deletion(
                record_id=record.id,
                actor_id=context["approver"].id,
            )
        record = await db.get(CaseRetentionRecord, record.id)
        assert record.status == STATUS_FAILED
        assert context["path"].exists()
        assert outside.exists()

    await engine.dispose()


@pytest.mark.asyncio
async def test_discovery_is_dry_run_by_default_and_persists_only_explicitly(
    tmp_path,
    monkeypatch,
):
    configure_retention(monkeypatch, tmp_path)
    engine, factory = await create_database(tmp_path, "retention-discovery.db")
    async with factory() as db:
        context = await seed_closed_case(db, tmp_path, suffix=5)
        await db.commit()
        service = CaseRetentionService(db)
        result = await service.discover_due_cases()
        assert result["dry_run"] is True
        assert result["due_count"] == 1
        assert (
            await db.execute(select(func.count(CaseRetentionRecord.id)))
        ).scalar_one() == 0

        result = await service.discover_due_cases(persist=True)
        await db.commit()
        assert result["dry_run"] is False
        assert result["created"] == 1
        record = (
            await db.execute(
                select(CaseRetentionRecord).where(
                    CaseRetentionRecord.case_id == context["case"].id
                )
            )
        ).scalar_one()
        assert record.status == STATUS_DISCOVERED

    await engine.dispose()


@pytest.mark.asyncio
async def test_existing_request_cannot_be_reassigned_to_another_admin(
    tmp_path,
    monkeypatch,
):
    configure_retention(monkeypatch, tmp_path)
    engine, factory = await create_database(tmp_path, "retention-request-owner.db")
    async with factory() as db:
        context = await seed_closed_case(db, tmp_path, suffix=6)
        service = CaseRetentionService(db)
        record = await service.request_deletion(
            case_id=context["case"].id,
            actor_id=context["requester"].id,
            reason="Первичный запрос после проверки срока хранения",
        )
        with pytest.raises(CaseRetentionError, match="запрос другого"):
            await service.request_deletion(
                case_id=context["case"].id,
                actor_id=context["approver"].id,
                reason="Попытка присвоить уже созданный запрос",
            )
        await db.refresh(record)
        assert record.requested_by == context["requester"].id

    await engine.dispose()


@pytest.mark.asyncio
async def test_recent_execution_lease_blocks_parallel_worker(
    tmp_path,
    monkeypatch,
):
    configure_retention(monkeypatch, tmp_path)
    engine, factory = await create_database(tmp_path, "retention-lease.db")
    async with factory() as db:
        context = await seed_closed_case(db, tmp_path, suffix=7)
        service = CaseRetentionService(db)
        record = await service.request_deletion(
            case_id=context["case"].id,
            actor_id=context["requester"].id,
            reason="Подготовлен запрос для проверки блокировки параллельного запуска",
        )
        record = await service.approve_deletion(
            record_id=record.id,
            actor_id=context["approver"].id,
            comment="Запрос проверен вторым суперадминистратором",
        )
        record.status = STATUS_EXECUTING
        record.execution_started_at = datetime.now(timezone.utc)
        await db.commit()

        with pytest.raises(CaseRetentionError, match="уже выполняется"):
            await service.execute_deletion(
                record_id=record.id,
                actor_id=context["approver"].id,
            )
        assert context["path"].exists()

    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_execution_lease_is_reclaimed_and_completed(
    tmp_path,
    monkeypatch,
):
    configure_retention(monkeypatch, tmp_path)
    engine, factory = await create_database(tmp_path, "retention-stale-lease.db")
    async with factory() as db:
        context = await seed_closed_case(db, tmp_path, suffix=8)
        service = CaseRetentionService(db)
        record = await service.request_deletion(
            case_id=context["case"].id,
            actor_id=context["requester"].id,
            reason="Подготовлен запрос для проверки восстановления после сбоя",
        )
        record = await service.approve_deletion(
            record_id=record.id,
            actor_id=context["approver"].id,
            comment="Запрос проверен перед тестом восстановления",
        )
        record.status = STATUS_EXECUTING
        record.execution_started_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.commit()

        completed = await service.execute_deletion(
            record_id=record.id,
            actor_id=context["approver"].id,
        )
        assert completed.status == STATUS_COMPLETED
        assert completed.attempt_count == 1
        assert not context["path"].exists()

    await engine.dispose()
