from __future__ import annotations

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base
from app.models.audit_chain_head import AuditChainHead
from app.models.audit_log import AuditLog
from app.security.audit_integrity import AuditIntegrityError, GENESIS_HASH, verify_audit_chain

AUDIT_OLD = "audit-old-" + "a" * 48
AUDIT_NEW = "audit-new-" + "b" * 48


def configure_audit_key(monkeypatch, *, key_id: str = "audit-old", secret: str = AUDIT_OLD):
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "audit_integrity_key_id", key_id)
    monkeypatch.setattr(settings, "audit_integrity_key", secret)
    monkeypatch.setattr(settings, "audit_integrity_previous_keys", "")


async def database(tmp_path, name: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, factory


def event(number: int) -> AuditLog:
    return AuditLog(
        actor_type="admin",
        actor_id=7,
        action=f"TEST_ACTION_{number}",
        entity_type="case",
        entity_id=100 + number,
        old_value={"status": "before", "number": number},
        new_value={"status": "after", "number": number},
        comment=f"Событие {number}",
    )


@pytest.mark.asyncio
async def test_multiple_events_in_one_flush_form_single_chain(tmp_path, monkeypatch):
    configure_audit_key(monkeypatch)
    engine, factory = await database(tmp_path, "audit-chain.db")

    async with factory() as db:
        db.add_all([event(1), event(2), event(3)])
        await db.commit()

    async with factory() as db:
        rows = (
            await db.execute(
                AuditLog.__table__.select().order_by(AuditLog.chain_sequence.asc())
            )
        ).mappings().all()
        assert [row["chain_sequence"] for row in rows] == [1, 2, 3]
        assert rows[0]["previous_hash"] == GENESIS_HASH
        assert rows[1]["previous_hash"] == rows[0]["event_hash"]
        assert rows[2]["previous_hash"] == rows[1]["event_hash"]
        assert all(row["integrity_key_id"] == "audit-old" for row in rows)
        result = await verify_audit_chain(db)
        assert result["ok"] is True
        assert result["checked_count"] == 3
        assert result["head_matches"] is True
        head = await db.get(AuditChainHead, 1)
        assert head.event_count == 3
        assert head.last_hash == rows[-1]["event_hash"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_orm_update_and_delete_are_rejected(tmp_path, monkeypatch):
    configure_audit_key(monkeypatch)
    engine, factory = await database(tmp_path, "audit-immutable.db")

    async with factory() as db:
        row = event(1)
        db.add(row)
        await db.commit()
        row.comment = "Попытка переписать историю"
        with pytest.raises(AuditIntegrityError):
            await db.commit()
        await db.rollback()

    async with factory() as db:
        row = await db.get(AuditLog, 1)
        await db.delete(row)
        with pytest.raises(AuditIntegrityError):
            await db.commit()
        await db.rollback()

    await engine.dispose()


@pytest.mark.asyncio
async def test_direct_middle_row_tampering_is_detected(tmp_path, monkeypatch):
    configure_audit_key(monkeypatch)
    engine, factory = await database(tmp_path, "audit-tamper.db")

    async with factory() as db:
        db.add_all([event(1), event(2), event(3)])
        await db.commit()
    async with engine.begin() as connection:
        await connection.execute(
            update(AuditLog.__table__)
            .where(AuditLog.__table__.c.chain_sequence == 2)
            .values(comment="Подменённое значение")
        )

    async with factory() as db:
        result = await verify_audit_chain(db)
        assert result["ok"] is False
        assert result["first_invalid"]["reason"] == "event_hash_mismatch"
        assert result["first_invalid"]["id"] == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_tail_deletion_is_detected_by_chain_head(tmp_path, monkeypatch):
    configure_audit_key(monkeypatch)
    engine, factory = await database(tmp_path, "audit-delete.db")

    async with factory() as db:
        db.add_all([event(1), event(2)])
        await db.commit()
    async with engine.begin() as connection:
        await connection.execute(
            delete(AuditLog.__table__).where(AuditLog.__table__.c.chain_sequence == 2)
        )

    async with factory() as db:
        result = await verify_audit_chain(db)
        assert result["ok"] is False
        assert result["first_invalid"]["reason"] == "chain_head_mismatch"
        assert result["head_event_count"] == 2
        assert result["event_count"] == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_audit_key_rotation_keeps_historical_verification(tmp_path, monkeypatch):
    configure_audit_key(monkeypatch)
    engine, factory = await database(tmp_path, "audit-rotation.db")

    async with factory() as db:
        db.add(event(1))
        await db.commit()

    monkeypatch.setattr(settings, "audit_integrity_key_id", "audit-new")
    monkeypatch.setattr(settings, "audit_integrity_key", AUDIT_NEW)
    monkeypatch.setattr(
        settings,
        "audit_integrity_previous_keys",
        f"audit-old:{AUDIT_OLD}",
    )
    async with factory() as db:
        db.add(event(2))
        await db.commit()
        result = await verify_audit_chain(db)
        assert result["ok"] is True
        assert result["key_ids"] == ["audit-new", "audit-old"]

    monkeypatch.setattr(settings, "audit_integrity_previous_keys", "")
    async with factory() as db:
        result = await verify_audit_chain(db)
        assert result["ok"] is False
        assert result["first_invalid"]["reason"] == "verification_key_missing"
        assert result["first_invalid"]["key_id"] == "audit-old"

    await engine.dispose()
