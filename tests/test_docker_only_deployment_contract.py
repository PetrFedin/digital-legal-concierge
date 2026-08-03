from __future__ import annotations

from pathlib import Path

import pytest

from app.bot.lease import TelegramPollingLease
from app.config import Settings
from scripts import production_preflight


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_container_owns_python_startup_and_canonical_process():
    dockerfile = read("Dockerfile")
    entrypoint = read("docker-entrypoint.sh")

    assert 'ENTRYPOINT ["dlc-entrypoint"]' in dockerfile
    assert 'CMD ["python", "-m", "app.process"]' in dockerfile
    assert "scripts/production_preflight.py" in entrypoint
    assert "scripts/init_db.py" in entrypoint
    assert "scripts/ensure_startup_backup.py" in entrypoint
    assert entrypoint.index("production_preflight") < entrypoint.index("init_db")
    assert entrypoint.index("init_db") < entrypoint.index("ensure_startup_backup")
    assert 'exec "$@"' in entrypoint


def test_operator_shell_scripts_do_not_require_host_python_or_virtualenv():
    paths = (
        "run.sh",
        "check-and-run.sh",
        "bot-control.sh",
        "backup.sh",
        "restore.sh",
        "status.sh",
        "acceptance.sh",
    )
    for path in paths:
        source = read(path)
        assert "python3 -m venv" not in source
        assert ".venv/bin/activate" not in source
        assert "pip install" not in source
        assert "docker compose" in source or "bash ./deploy.sh" in source


def test_compose_persists_database_documents_and_backups():
    local_compose = read("docker-compose.yml")
    timeweb_compose = read("docker-compose.timeweb.yml")

    assert "sqlite+aiosqlite:////app/data/legal_bot.db" in local_compose
    for mount in ("/app/data", "/app/storage", "/app/backups", "/app/logs"):
        assert mount in local_compose
        assert mount in timeweb_compose
    assert "stop_grace_period: 45s" in local_compose
    assert "stop_grace_period: 45s" in timeweb_compose


def test_production_template_is_fail_closed_for_demo_and_browser_token_query():
    template = read(".env.production.example")

    assert "APP_ENV=production" in template
    assert "RUN_BOT=true" in template
    assert "RUN_SCHEDULER=true" in template
    assert "BOOTSTRAP_DEMO_DATA=false" in template
    assert "ALLOW_TOKEN_QUERY=false" in template
    assert "ENABLE_RECOVERY_ACTIONS=false" in template
    assert "REQUIRE_POSTGRES_IN_PRODUCTION=true" in template
    assert "STARTUP_BACKUP_ENABLED=true" in template


def test_demo_lawyer_bootstrap_is_explicit():
    source = read("scripts/init_db.py")

    assert "if settings.bootstrap_demo_data:" in source
    assert source.index("if settings.bootstrap_demo_data:") < source.index(
        "await get_or_create_lawyer(db)"
    )
    assert "if settings.bootstrap_admin:" in source


def test_settings_include_container_and_telegram_restart_policy():
    settings = Settings(_env_file=None)

    assert settings.bootstrap_demo_data is False
    assert settings.bootstrap_admin is True
    assert settings.startup_backup_enabled is True
    assert settings.telegram_drop_pending_updates is False
    assert settings.telegram_singleton_wait_seconds == 120
    assert settings.telegram_singleton_retry_seconds == 3


def test_local_preflight_does_not_require_or_expose_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(production_preflight.settings, "app_env", "local")
    monkeypatch.setattr(
        production_preflight.settings,
        "storage_dir",
        str(tmp_path / "storage"),
    )
    monkeypatch.setattr(
        production_preflight.settings,
        "backup_dir",
        str(tmp_path / "backups"),
    )

    report = production_preflight.build_report()

    assert report["ok"] is True
    assert report["secrets_exposed"] is False
    assert report["failed"] == []


@pytest.mark.asyncio
async def test_sqlite_telegram_polling_lease_is_singleton_and_reusable(tmp_path):
    first = TelegramPollingLease(lock_dir=tmp_path, dialect_name="sqlite")
    second = TelegramPollingLease(lock_dir=tmp_path, dialect_name="sqlite")

    assert await first.acquire_once() is True
    assert await second.acquire_once() is False

    await first.release()
    assert await second.acquire_once() is True
    await second.release()
