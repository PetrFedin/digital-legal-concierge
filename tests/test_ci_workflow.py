from __future__ import annotations

from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_ci_uses_current_node24_actions_without_persisted_credentials():
    text = workflow_text()

    assert "actions/checkout@v6" in text
    assert "actions/setup-python@v6" in text
    assert "actions/checkout@v4" not in text
    assert "actions/setup-python@v5" not in text
    assert text.count("persist-credentials: false") == 3


def test_ci_has_least_privilege_and_cancels_obsolete_runs():
    text = workflow_text()

    assert "permissions:\n  contents: read" in text
    assert "concurrency:" in text
    assert "cancel-in-progress: true" in text
    assert "branches: [main, feature/multi-role-access]" not in text


def test_ci_verifies_source_and_restored_database_schemas():
    text = workflow_text()

    assert "sqlite-tests:" in text
    assert "postgres-migrations:" in text
    assert "postgres:16-alpine" in text
    assert "postgresql+asyncpg://" in text
    # SQLite source, PostgreSQL source and restored PostgreSQL staging database.
    assert text.count("alembic check") == 3
    assert text.count("alembic upgrade head") >= 4


def test_ci_runs_real_postgresql_backup_and_restore_drill():
    text = workflow_text()

    assert "PostgreSQL migration, backup and restore drill" in text
    assert "postgresql-client" in text
    assert "backup_restore_probe" in text
    assert "verified-data-roundtrip" in text
    assert "app.security.backup_cli create" in text
    assert "app.security.backup_cli verify" in text
    assert "restore-postgresql-staging" in text
    assert "STAGING_DATABASE_URL" in text
    assert "createdb" in text
    assert "--confirm-database concierge_restore" in text
    assert 'restored["schema_current"] is True' in text
    assert 'restored["restored_revision"] == restored["expected_revision"]' in text
    assert "DATABASE_URL=\"$STAGING_DATABASE_URL\" alembic check" in text
    assert '"engine": "postgresql"' in text
    assert '"format": "pg_dump_custom"' in text
    assert "-name '.pgpass'" in text


def test_ci_builds_and_starts_the_production_container():
    text = workflow_text()

    assert "container-smoke:" in text
    assert "docker build --tag digital-legal-concierge:ci ." in text
    assert "test -f /app/alembic.ini" in text
    assert "test -d /app/migrations/versions" in text
    assert "pg_dump --version" in text
    assert "digital-legal-concierge:ci" in text
    assert "http://127.0.0.1:18080/health" in text
    assert "Container exited before becoming healthy" in text


def test_every_ci_job_has_a_timeout_and_fixed_runner_image():
    text = workflow_text()

    assert text.count("runs-on: ubuntu-24.04") == 3
    assert text.count("timeout-minutes:") == 3
