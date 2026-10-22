"""Client POA history contains public facts while preserving sealed staff notes."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.screens.history import _format_timeline
from app.config import settings
from app.domain.cases.case_activity import (
    CaseActivityService,
    CaseActivityUnavailable,
    present_case_activity,
)
from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.m1_process_service import M1ProcessService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.case_retention import CaseRetentionRecord
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.user import User
from app.security.audit_integrity import verify_audit_chain


INTERNAL_NOTE = "INTERNAL-POA-COURIER-NOTE-DO-NOT-SHOW"
RAW_PAYLOAD_SECRET = "PRIVATE-POA-RAW-PAYLOAD"


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "audit_integrity_key_id", "poa-history-test")
    monkeypatch.setattr(settings, "audit_integrity_key", "poa-history-" + "s" * 48)
    monkeypatch.setattr(settings, "audit_integrity_previous_keys", "")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'poa-history.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def record_poa_flow(db):
    client = User(telegram_id=990221001, full_name="Клиент истории")
    lawyer = Lawyer(full_name="Юрист истории")
    db.add_all([client, lawyer])
    await db.flush()
    case = Case(
        client_id=client.id, case_number="POA-HISTORY-1", route="M1",
        status=CaseStatus.M1_POWER_OF_ATTORNEY, assigned_lawyer_id=lawyer.id,
        poa_client_confirmed_at=datetime.now(timezone.utc),
    )
    db.add(case)
    await db.flush()
    db.add(Payment(
        case_id=case.id, payment_code=PaymentCode.M1_INITIAL_PAYMENT,
        title="Первый платёж", amount=Decimal("30000.00"), status=PaymentStatus.PAID,
    ))
    await db.flush()
    ready = await add_case_history_event(
        db, actor_type="client", actor_id=client.id, case_id=case.id,
        action="POA_READY_REPORTED",
        old_value={"internal_note": RAW_PAYLOAD_SECRET},
        new_value={
            "poa_client_confirmed_at": case.poa_client_confirmed_at.isoformat(),
            "message_id": 898989,
            "internal_note": RAW_PAYLOAD_SECRET,
        },
        comment=RAW_PAYLOAD_SECRET,
    )
    await M1ProcessService(db).mark_poa_received(
        case=case, lawyer_id=lawyer.id, comment=INTERNAL_NOTE,
    )
    received = (await db.scalars(select(AuditLog).where(
        AuditLog.entity_id == case.id, AuditLog.action == "POA_RECEIVED",
    ))).one()
    await db.commit()
    return case, ready.id, received.id


async def test_actual_poa_flow_has_two_clear_client_events_without_staff_notes(store):
    async with store() as db:
        case, ready_id, received_id = await record_poa_flow(db)
        receipt_audit = await db.get(AuditLog, received_id)
        source_before = deepcopy((
            receipt_audit.comment, receipt_audit.old_value,
            receipt_audit.new_value, receipt_audit.event_hash,
        ))

        page = await CaseActivityService(db).page(
            case_id=case.id, audience="client", limit=20,
        )
        by_code = {item["code"]: item for item in page["items"]}
        ready = by_code["POA_READY_REPORTED"]
        receipt = by_code["POA_RECEIVED"]
        assert ready["id"] == ready_id
        assert receipt["id"] == received_id
        assert ready["title"] == "Сообщение о готовности доверенности сохранено"
        assert ready["detail"] == "Команда проверит получение доверенности и заверенных копий."
        assert receipt["title"] == "Получение доверенности подтверждено"
        assert receipt["detail"] == "Доверенность и заверенные копии получены юридической командой."
        assert ready["category"] == receipt["category"] == "documents"
        assert ready["actor_label"] == "Клиент"
        assert receipt["actor_label"] == "Юрист"
        assert ready["occurred_at"] and receipt["occurred_at"]
        assert "CASE_STATUS_CHANGED" in by_code
        assert "SLA_ACTION_RECORDED" not in by_code

        # The same staff comment is written to POA_RECEIVED and the status
        # transition. Neither the JSON projection nor Telegram may reveal it.
        rendered = json.dumps(page, ensure_ascii=False) + _format_timeline(page)
        for secret in (
            INTERNAL_NOTE, RAW_PAYLOAD_SECRET, "internal_note", "payment_id",
            "message_id", "old_value", "new_value", "event_hash",
        ):
            assert secret not in rendered
        assert ready["title"] in _format_timeline(page)
        assert receipt["title"] in _format_timeline(page)

        await db.refresh(receipt_audit)
        assert (
            receipt_audit.comment, receipt_audit.old_value,
            receipt_audit.new_value, receipt_audit.event_hash,
        ) == source_before
        assert receipt_audit.comment == INTERNAL_NOTE
        assert (await verify_audit_chain(db))["ok"] is True


async def test_poa_history_pagination_remains_scoped_to_the_selected_case(store):
    async with store() as db:
        case, ready_id, received_id = await record_poa_flow(db)
        other_client = User(telegram_id=990221002, full_name="Другой клиент")
        db.add(other_client)
        await db.flush()
        other_case = Case(
            client_id=other_client.id, case_number="POA-HISTORY-OTHER",
            route="M1", status=CaseStatus.M1_POWER_OF_ATTORNEY,
        )
        db.add(other_case)
        await db.flush()
        other = await add_case_history_event(
            db, actor_type="client", actor_id=other_client.id,
            case_id=other_case.id, action="POA_READY_REPORTED",
            comment="OTHER-CLIENT-PRIVATE-NOTE",
        )
        await db.commit()

        activity = CaseActivityService(db)
        cursor = None
        seen = []
        for _ in range(10):
            page = await activity.page(
                case_id=case.id, audience="client", before_id=cursor, limit=1,
            )
            assert page["case_id"] == case.id
            seen.extend(item["id"] for item in page["items"])
            if not page["has_more"]:
                break
            assert page["next_before_id"] is not None
            cursor = page["next_before_id"]
        else:
            pytest.fail("История не завершилась в пределах ожидаемых событий")
        assert ready_id in seen and received_id in seen
        assert other.id not in seen
        assert len(seen) == len(set(seen))


@pytest.mark.parametrize("gate", ["ERASED", "APPROVED"])
async def test_new_poa_events_obey_existing_retention_read_boundary(store, gate):
    async with store() as db:
        case, _, received_id = await record_poa_flow(db)
        if gate == "ERASED":
            case.content_deleted_at = datetime.now(timezone.utc)
        else:
            db.add(CaseRetentionRecord(
                case_id=case.id, status="APPROVED",
                retention_due_at=datetime.now(timezone.utc),
            ))
        await db.commit()
        with pytest.raises(CaseActivityUnavailable):
            await CaseActivityService(db).page(case_id=case.id, audience="client")
        assert (await db.get(AuditLog, received_id)).comment == INTERNAL_NOTE
        assert (await verify_audit_chain(db))["ok"] is True


@pytest.mark.parametrize("action", ["POA_READY_REPORTED", "POA_RECEIVED"])
@pytest.mark.parametrize("payload", [None, [RAW_PAYLOAD_SECRET], {
    "comment": RAW_PAYLOAD_SECRET, "client_comment": RAW_PAYLOAD_SECRET,
    "poa_received_at": RAW_PAYLOAD_SECRET,
}])
def test_poa_projection_never_falls_back_to_arbitrary_payload_or_comment(action, payload):
    item = present_case_activity(SimpleNamespace(
        id=1, created_at=datetime.now(timezone.utc), action=action,
        new_value=payload, comment=INTERNAL_NOTE, actor_type="lawyer",
    ), audience="client")
    assert item is not None
    assert item.detail
    assert INTERNAL_NOTE not in json.dumps(item.as_dict())
    assert RAW_PAYLOAD_SECRET not in json.dumps(item.as_dict())
