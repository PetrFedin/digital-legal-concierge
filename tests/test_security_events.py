from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base
from app.models.audit_log import AuditLog
from app.security.audit_integrity import verify_audit_chain
from app.security.security_events import (
    pseudonymize_security_value,
    record_security_event,
    sanitize_security_details,
    security_event_severity,
)


def configure_test_keys(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "security_hmac_key_id", "hmac-test")
    monkeypatch.setattr(settings, "security_hmac_key", "h" * 48)
    monkeypatch.setattr(settings, "security_hmac_previous_keys", "")
    monkeypatch.setattr(settings, "audit_integrity_key_id", "audit-test")
    monkeypatch.setattr(settings, "audit_integrity_key", "a" * 48)
    monkeypatch.setattr(settings, "audit_integrity_previous_keys", "")


def test_security_details_redact_nested_credentials_and_bound_size():
    result = sanitize_security_details(
        {
            "password": "SuperSecret",
            "nested": {
                "authorization": "Bearer abc",
                "safe": "visible",
                "api_token": "token-value",
            },
            "long": "x" * 700,
            "items": list(range(70)),
        }
    )

    assert result["password"] == "[redacted]"
    assert result["nested"]["authorization"] == "[redacted]"
    assert result["nested"]["api_token"] == "[redacted]"
    assert result["nested"]["safe"] == "visible"
    assert len(result["long"]) <= 501
    assert len(result["items"]) == 50


def test_pseudonym_is_stable_and_does_not_contain_source(monkeypatch):
    configure_test_keys(monkeypatch)

    first = pseudonymize_security_value("client", "203.0.113.18")
    second = pseudonymize_security_value("client", "203.0.113.18")
    other = pseudonymize_security_value("client", "203.0.113.19")

    assert first == second
    assert first != other
    assert first.startswith("hmac-test:")
    assert "203.0.113.18" not in first


def test_security_event_severity_classification():
    assert security_event_severity("security.admin_login_locked") == "critical"
    assert (
        security_event_severity(
            "security.mfa_login",
            {"method": "recovery_code"},
        )
        == "warning"
    )
    assert security_event_severity("DOCUMENT_UPLOAD_REJECTED") == "warning"
    assert security_event_severity("security.admin_password_authenticated") == "info"


async def test_recorded_security_event_is_redacted_pseudonymized_and_sealed(
    monkeypatch,
):
    configure_test_keys(monkeypatch)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        await record_security_event(
            db,
            action="security.origin_blocked",
            severity="warning",
            source="test",
            principal="Admin@Example.test",
            client_address="203.0.113.18",
            resource_type="http_request",
            details={
                "reason": "origin_not_allowed",
                "path": "/admin/change",
                "password": "must-not-persist",
                "authorization": "Bearer must-not-persist",
            },
            comment="Отклонён тестовый запрос",
        )
        await db.commit()

        event = (await db.execute(select(AuditLog))).scalar_one()
        verification = await verify_audit_chain(db)

    serialized = str(event.new_value)
    assert event.chain_sequence == 1
    assert event.event_hash and len(event.event_hash) == 64
    assert event.integrity_key_id == "audit-test"
    assert event.new_value["severity"] == "warning"
    assert event.new_value["principal_ref"].startswith("hmac-test:")
    assert event.new_value["client_ref"].startswith("hmac-test:")
    assert event.new_value["details"]["password"] == "[redacted]"
    assert event.new_value["details"]["authorization"] == "[redacted]"
    assert "Admin@Example.test" not in serialized
    assert "203.0.113.18" not in serialized
    assert "must-not-persist" not in serialized
    assert verification["ok"] is True
    assert verification["checked_count"] == 1

    await engine.dispose()
