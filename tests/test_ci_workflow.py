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
    # Every checkout in the seven CI jobs must disable credential persistence.
    assert text.count("persist-credentials: false") == 7


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
    # PM-018 + PM-016 isolated proofs, SQLite source, PostgreSQL source and restored staging.
    assert text.count("alembic check") == 5
    assert text.count("alembic upgrade head") >= 4


def test_ci_verifies_real_postgresql_scheduler_singleton_lease():
    text = workflow_text()

    assert "Verify PostgreSQL scheduler singleton lease" in text
    assert "from app.scheduler.lease import SchedulerCycleLease" in text
    assert "assert await first.acquire() is True" in text
    assert "assert await second.acquire() is False" in text
    assert "await first.assert_held()" in text
    assert "assert await second.acquire() is True" in text
    assert "await second.assert_held()" in text


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

    assert text.count("runs-on: ubuntu-24.04") == 7
    assert text.count("timeout-minutes:") == 7


def test_ci_has_dedicated_pm018_authority_proof():
    text = workflow_text()

    assert "pm018-authority-proof:" in text
    assert "PM-018 authority and M2 handoff proof" in text
    assert "20260918_0024_case_transition_authority_recovery.py" in text
    assert "tests/test_case_transition_authority_pm018.py" in text
    assert "tests/test_m2_to_m1_authority_pm018.py" in text
    assert "Prove migration 0024 and ORM parity" in text
    assert "Run PM-018 regression and negative proof" in text


def test_ci_has_dedicated_pm019_projection_proof():
    text = workflow_text()

    assert "pm019-projection-proof:" in text
    assert "PM-019 My Case projection proof" in text
    assert "tests/test_pm019_my_case_projection.py" in text
    assert "Run PM-019 deterministic projection proof" in text


def test_ci_has_dedicated_pm016_v2_legal_rule_editor_proof():
    text = workflow_text()

    assert "pm016-rule-editor-proof:" in text
    assert "PM-016 v2 legal rule editor proof" in text
    assert "20260924_0025_calculator_rule_v2_legal_review.py" in text
    assert "tests/test_calculator_rule_engine_v2.py" in text
    assert "tests/test_calculator_rule_lifecycle_v2.py" in text
    assert "Prove migration 0025 and ORM parity" in text
    assert "Run PM-016 v2 bounded regression proof" in text
