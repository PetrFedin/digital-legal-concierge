from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db.migrations import run_database_migrations


HEAD_REVISION = "20260730_0009"


def sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    return {row[0] for row in rows}


def column_names(path: Path, table_name: str) -> set[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def current_revision(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]


def test_fresh_database_migrates_to_head_and_is_idempotent(tmp_path):
    database_path = tmp_path / "fresh-migrations.db"
    url = sqlite_url(database_path)

    run_database_migrations(database_url=url)
    run_database_migrations(database_url=url)

    tables = table_names(database_path)
    assert {
        "users",
        "lawyers",
        "cases",
        "payments",
        "payment_webhook_events",
        "consultations",
        "consultation_slots",
        "notifications",
        "audit_logs",
        "audit_chain_heads",
        "system_settings",
        "login_security_states",
        "revoked_access_tokens",
        "document_access_grants",
        "alembic_version",
    }.issubset(tables)
    assert current_revision(database_path) == HEAD_REVISION
    assert {
        "assigned_at",
        "first_lawyer_response_at",
        "last_lawyer_activity_at",
        "sla_due_at",
        "sla_status",
        "escalation_level",
    }.issubset(column_names(database_path, "cases"))
    assert {
        "recipient_type",
        "target_chat_id",
        "dedupe_key",
        "attempt_count",
        "last_error",
        "next_attempt_at",
        "sent_at",
    }.issubset(column_names(database_path, "notifications"))
    assert {
        "mfa_enabled",
        "mfa_secret_encrypted",
        "mfa_confirmed_at",
        "mfa_recovery_codes",
        "mfa_recovery_codes_generated_at",
        "mfa_failed_attempts",
        "mfa_locked_until",
        "mfa_last_totp_step",
        "session_version",
    }.issubset(column_names(database_path, "admin_users"))
    assert {
        "key_hash",
        "failed_attempts",
        "window_started_at",
        "last_attempt_at",
        "locked_until",
    }.issubset(column_names(database_path, "login_security_states"))
    assert {
        "token_hash",
        "user_id",
        "expires_at",
        "reason",
        "comment",
    }.issubset(column_names(database_path, "revoked_access_tokens"))
    assert {
        "sha256",
        "detected_type",
        "security_status",
        "security_reason",
        "scanned_at",
        "encryption_status",
        "encryption_key_id",
        "encryption_error",
        "encrypted_at",
    }.issubset(column_names(database_path, "documents"))
    assert {
        "public_id",
        "document_id",
        "case_id",
        "actor_account_id",
        "actor_role",
        "session_jti_ref",
        "session_version",
        "token_key_id",
        "token_digest",
        "expires_at",
        "used_at",
        "revoked_at",
        "client_ref",
    }.issubset(column_names(database_path, "document_access_grants"))
    assert {
        "calculation_date",
        "key_rate",
        "consumer_multiplier",
        "formula_version",
    }.issubset(column_names(database_path, "calculations"))
    assert {
        "provider",
        "event_key",
        "event_type",
        "provider_payment_id",
        "payment_id",
        "payload_sha256",
        "payload_summary",
        "status",
        "attempt_count",
        "first_seen_at",
        "last_seen_at",
        "processing_started_at",
        "processed_at",
        "response_code",
        "error_code",
    }.issubset(column_names(database_path, "payment_webhook_events"))
    assert {
        "chain_version",
        "chain_sequence",
        "previous_hash",
        "event_hash",
        "integrity_key_id",
        "sealed_at",
    }.issubset(column_names(database_path, "audit_logs"))
    with sqlite3.connect(database_path) as connection:
        head = connection.execute(
            "SELECT event_count, last_hash FROM audit_chain_heads WHERE id=1"
        ).fetchone()
    assert head == (0, "0" * 64)


def create_legacy_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE admin_users (
                id INTEGER PRIMARY KEY,
                full_name VARCHAR(255) NOT NULL,
                email VARCHAR(255) NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(100) NOT NULL,
                is_active BOOLEAN NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE TABLE lawyers (
                id INTEGER PRIMARY KEY,
                full_name VARCHAR(255) NOT NULL,
                phone VARCHAR(50),
                email VARCHAR(255),
                specialization VARCHAR(255),
                is_active BOOLEAN NOT NULL,
                workload_limit INTEGER NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE TABLE cases (
                id INTEGER PRIMARY KEY,
                case_number VARCHAR(50) NOT NULL,
                client_id INTEGER NOT NULL,
                route VARCHAR(10),
                status VARCHAR(100) NOT NULL,
                title VARCHAR(255),
                source VARCHAR(100) NOT NULL,
                assigned_lawyer_id INTEGER,
                next_action VARCHAR(255),
                internal_comment TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE TABLE consultations (
                id INTEGER PRIMARY KEY,
                case_id INTEGER NOT NULL,
                lawyer_id INTEGER,
                status VARCHAR(100) NOT NULL,
                scheduled_at DATETIME,
                client_description TEXT,
                lawyer_result TEXT,
                decision VARCHAR(100),
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE TABLE payments (
                id INTEGER PRIMARY KEY,
                case_id INTEGER NOT NULL,
                payment_code VARCHAR(100) NOT NULL,
                title VARCHAR(255) NOT NULL,
                amount NUMERIC(14, 2) NOT NULL,
                currency VARCHAR(10) NOT NULL,
                status VARCHAR(100) NOT NULL,
                provider VARCHAR(100),
                provider_payment_id VARCHAR(255),
                confirmation_url VARCHAR(500),
                expires_at DATETIME,
                paid_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE TABLE notifications (
                id INTEGER PRIMARY KEY,
                case_id INTEGER,
                user_id INTEGER,
                channel VARCHAR(50) NOT NULL,
                event_code VARCHAR(100) NOT NULL,
                title VARCHAR(255),
                text TEXT NOT NULL,
                status VARCHAR(100) NOT NULL,
                is_sent BOOLEAN NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE TABLE audit_logs (
                id INTEGER PRIMARY KEY,
                actor_type VARCHAR(50) NOT NULL,
                actor_id INTEGER,
                action VARCHAR(100) NOT NULL,
                entity_type VARCHAR(100) NOT NULL,
                entity_id INTEGER,
                old_value JSON,
                new_value JSON,
                comment TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            INSERT INTO cases (
                id, case_number, client_id, route, status, title, source,
                assigned_lawyer_id, next_action, internal_comment,
                created_at, updated_at
            ) VALUES (
                7, 'LEGACY-0007', 99, 'M1', 'M1_LAWYER_REVIEW',
                'Существующее дело', 'telegram_bot', NULL,
                'Ожидать проверки', 'Не удалять',
                '2026-07-01 10:00:00', '2026-07-01 10:00:00'
            );
            INSERT INTO audit_logs (
                id, actor_type, actor_id, action, entity_type, entity_id,
                old_value, new_value, comment, created_at, updated_at
            ) VALUES (
                3, 'admin', 1, 'LEGACY_EVENT', 'case', 7,
                '{"status":"old"}', '{"status":"new"}', 'Историческая запись',
                '2026-07-01 10:01:00', '2026-07-01 10:01:00'
            );
            """
        )
        connection.commit()


def test_legacy_database_is_adopted_without_data_loss(tmp_path):
    database_path = tmp_path / "legacy-migrations.db"
    create_legacy_database(database_path)

    run_database_migrations(database_url=sqlite_url(database_path))
    run_database_migrations(database_url=sqlite_url(database_path))

    assert current_revision(database_path) == HEAD_REVISION
    assert {
        "login_security_states",
        "revoked_access_tokens",
        "audit_chain_heads",
        "document_access_grants",
        "payment_webhook_events",
    }.issubset(table_names(database_path))
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT case_number, title, internal_comment, sla_status, "
            "escalation_level FROM cases WHERE id=7"
        ).fetchone()
        audit = connection.execute(
            "SELECT action, chain_version, chain_sequence, previous_hash, "
            "event_hash, integrity_key_id FROM audit_logs WHERE id=3"
        ).fetchone()
        head = connection.execute(
            "SELECT event_count, last_hash FROM audit_chain_heads WHERE id=1"
        ).fetchone()
    assert row == (
        "LEGACY-0007",
        "Существующее дело",
        "Не удалять",
        "NOT_STARTED",
        0,
    )
    assert audit[0:4] == ("LEGACY_EVENT", 1, 1, "0" * 64)
    assert len(audit[4]) == 64
    assert audit[5] == "legacy-admin"
    assert head == (1, audit[4])

    assert {
        "username",
        "telegram_id",
        "mfa_enabled",
        "mfa_secret_encrypted",
        "mfa_confirmed_at",
        "mfa_recovery_codes",
        "mfa_recovery_codes_generated_at",
        "mfa_failed_attempts",
        "mfa_locked_until",
        "mfa_last_totp_step",
        "session_version",
    }.issubset(column_names(database_path, "admin_users"))
    assert "telegram_id" in column_names(database_path, "lawyers")
    assert {"related_case_id", "slot_id", "subject_type"}.issubset(
        column_names(database_path, "consultations")
    )
    assert "reservation_key" in column_names(database_path, "payments")
    assert {
        "dedupe_key",
        "attempt_count",
        "next_attempt_at",
        "sent_at",
    }.issubset(column_names(database_path, "notifications"))
    assert {
        "sha256",
        "detected_type",
        "security_status",
        "security_reason",
        "scanned_at",
        "encryption_status",
        "encryption_key_id",
        "encryption_error",
        "encrypted_at",
    }.issubset(column_names(database_path, "documents"))
    assert {
        "chain_version",
        "chain_sequence",
        "previous_hash",
        "event_hash",
        "integrity_key_id",
        "sealed_at",
    }.issubset(column_names(database_path, "audit_logs"))
