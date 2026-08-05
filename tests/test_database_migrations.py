from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db.migrations import run_database_migrations

HEAD_REVISION = "20260805_0012"
RETENTION_TRIGGER = "trg_retention_destroy_document_keys"
MESSAGE_SOURCE_INDEX = "uq_messages_sender_source_message"


def sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    return {row[0] for row in rows}


def trigger_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall()
    return {row[0] for row in rows}


def index_names(path: Path, table_name: str) -> set[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(f"PRAGMA index_list({table_name})").fetchall()
    return {row[1] for row in rows}


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
        "documents",
        "messages",
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
        "case_retention_records",
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
        "closed_at",
        "content_deleted_at",
    }.issubset(column_names(database_path, "cases"))
    assert {
        "sha256",
        "detected_type",
        "security_status",
        "security_reason",
        "scanned_at",
        "encryption_status",
        "encryption_key_id",
        "encryption_format_version",
        "encryption_envelope_id",
        "encrypted_data_key",
        "encrypted_data_key_nonce",
        "data_key_destroyed_at",
        "encryption_error",
        "encrypted_at",
    }.issubset(column_names(database_path, "documents"))
    assert "source_message_id" in column_names(database_path, "messages")
    assert MESSAGE_SOURCE_INDEX in index_names(database_path, "messages")
    assert {
        "case_id",
        "policy_version",
        "status",
        "retention_due_at",
        "legal_hold",
        "requested_by",
        "approved_by",
        "execution_started_at",
        "executed_at",
        "attempt_count",
        "documents_deleted",
        "content_digest",
    }.issubset(column_names(database_path, "case_retention_records"))
    assert {
        "provider",
        "event_key",
        "event_type",
        "provider_payment_id",
        "payment_id",
        "payload_sha256",
        "status",
        "attempt_count",
        "processed_at",
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
    assert RETENTION_TRIGGER in trigger_names(database_path)
    assert "ux_documents_encryption_envelope_id" in index_names(
        database_path, "documents"
    )
    assert "ix_documents_data_key_destroyed_at" in index_names(
        database_path, "documents"
    )
    with sqlite3.connect(database_path) as connection:
        head = connection.execute(
            "SELECT event_count, last_hash FROM audit_chain_heads WHERE id=1"
        ).fetchone()
    assert head == (0, "0" * 64)


def _insert_encrypted_document(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    case_id: int,
    format_version: int,
    envelope_id: str,
) -> None:
    connection.execute(
        """
        INSERT INTO documents (
            id, case_id, uploaded_by_user_id, document_type, title,
            file_name, file_path, mime_type, file_size, sha256,
            detected_type, security_status, security_reason, scanned_at,
            encryption_status, encryption_key_id, encryption_format_version,
            encryption_envelope_id, encrypted_data_key,
            encrypted_data_key_nonce, data_key_destroyed_at,
            encryption_error, encrypted_at, version, status, is_required,
            created_at, updated_at
        ) VALUES (
            ?, ?, NULL, 'DDU', 'ДДУ', 'contract.pdf', ?,
            'application/pdf', 100, ?, 'pdf', 'VERIFIED', NULL,
            CURRENT_TIMESTAMP, 'ENCRYPTED', 'documents-test', ?, ?,
            'wrapped-key', 'wrapped-nonce', NULL, NULL,
            CURRENT_TIMESTAMP, 1, 'ON_REVIEW', 1,
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        """,
        (
            document_id,
            case_id,
            f"/storage/{document_id}.dlcenc",
            f"{document_id:064x}",
            format_version,
            envelope_id,
        ),
    )


def test_retention_trigger_destroys_v2_keys_atomically_before_file_deletion(tmp_path):
    database_path = tmp_path / "retention-trigger.db"
    run_database_migrations(database_url=sqlite_url(database_path))

    with sqlite3.connect(database_path) as connection:
        _insert_encrypted_document(
            connection,
            document_id=1,
            case_id=77,
            format_version=2,
            envelope_id="1" * 32,
        )
        _insert_encrypted_document(
            connection,
            document_id=2,
            case_id=77,
            format_version=1,
            envelope_id="2" * 32,
        )
        _insert_encrypted_document(
            connection,
            document_id=3,
            case_id=88,
            format_version=2,
            envelope_id="3" * 32,
        )
        connection.execute(
            """
            INSERT INTO case_retention_records (
                id, case_id, policy_version, status, retention_due_at,
                legal_hold, attempt_count, documents_deleted,
                messages_deleted, notifications_deleted,
                consultations_anonymized, created_at, updated_at
            ) VALUES (
                1, 77, 'case-content-v1', 'APPROVED', CURRENT_TIMESTAMP,
                0, 0, 0, 0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """
        )
        connection.commit()

        connection.execute("BEGIN")
        connection.execute(
            "UPDATE case_retention_records SET status='EXECUTING' WHERE id=1"
        )
        during_transaction = connection.execute(
            "SELECT encrypted_data_key, data_key_destroyed_at "
            "FROM documents WHERE id=1"
        ).fetchone()
        assert during_transaction[0] is None
        assert during_transaction[1] is not None
        connection.rollback()
        after_rollback = connection.execute(
            "SELECT encrypted_data_key, data_key_destroyed_at "
            "FROM documents WHERE id=1"
        ).fetchone()
        assert after_rollback == ("wrapped-key", None)

        connection.execute(
            "UPDATE case_retention_records SET status='EXECUTING' WHERE id=1"
        )
        connection.commit()
        rows = connection.execute(
            "SELECT id, encrypted_data_key, encrypted_data_key_nonce, "
            "data_key_destroyed_at FROM documents ORDER BY id"
        ).fetchall()

    assert rows[0][0:3] == (1, None, None)
    assert rows[0][3] is not None
    assert rows[1] == (2, "wrapped-key", "wrapped-nonce", None)
    assert rows[2] == (3, "wrapped-key", "wrapped-nonce", None)


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
        "case_retention_records",
        "messages",
    }.issubset(table_names(database_path))
    assert RETENTION_TRIGGER in trigger_names(database_path)
    assert {
        "encryption_format_version",
        "encryption_envelope_id",
        "encrypted_data_key",
        "encrypted_data_key_nonce",
        "data_key_destroyed_at",
    }.issubset(column_names(database_path, "documents"))
    assert "source_message_id" in column_names(database_path, "messages")
    assert MESSAGE_SOURCE_INDEX in index_names(database_path, "messages")
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
