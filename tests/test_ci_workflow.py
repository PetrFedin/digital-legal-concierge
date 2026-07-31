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
    assert text.count("persist-credentials: false") == 2


def test_ci_has_least_privilege_and_cancels_obsolete_runs():
    text = workflow_text()

    assert "permissions:\n  contents: read" in text
    assert "concurrency:" in text
    assert "cancel-in-progress: true" in text
    assert "branches: [main, feature/multi-role-access]" not in text


def test_ci_verifies_both_sqlite_and_postgresql_schemas():
    text = workflow_text()

    assert "sqlite-tests:" in text
    assert "postgres-migrations:" in text
    assert "postgres:16-alpine" in text
    assert "postgresql+asyncpg://" in text
    assert text.count("alembic check") == 2
    assert text.count("alembic upgrade head") >= 4


def test_every_ci_job_has_a_timeout_and_fixed_runner_image():
    text = workflow_text()

    assert text.count("runs-on: ubuntu-24.04") == 2
    assert text.count("timeout-minutes:") == 2
