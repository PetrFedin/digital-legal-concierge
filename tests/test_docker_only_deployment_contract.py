from __future__ import annotations

from pathlib import Path

import pytest

from app.bot.lease import TelegramPollingLease
from app.config import Settings
from app.db.migrations import build_alembic_config, resolve_project_root
from scripts import production_preflight


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_container_owns_python_startup_and_canonical_process():
    dockerfile = read("Dockerfile")
    entrypoint = read("docker-entrypoint.sh")

    assert 'ENTRYPOINT ["dlc-entrypoint"]' in dockerfile
    assert 'CMD ["python", "-m", "app.process"]' in dockerfile
    for command in (
        "scripts/production_preflight.py",
        "scripts/wait_for_redis.py",
        "scripts/wait_for_database.py",
        "scripts/init_db.py",
        "scripts/ensure_startup_backup.py",
    ):
        assert command in entrypoint
    assert entrypoint.index("production_preflight") < entrypoint.index("wait_for_redis")
    assert entrypoint.index("wait_for_redis") < entrypoint.index("wait_for_database")
    assert entrypoint.index("wait_for_database") < entrypoint.index("init_db")
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
        "test.sh",
    )
    for path in paths:
        source = read(path)
        assert "python3 -m venv" not in source
        assert ".venv/bin/activate" not in source
        assert "pip install" not in source
        assert (
            "docker compose" in source
            or "docker build" in source
            or "bash ./deploy.sh" in source
        )


def test_test_dependencies_are_isolated_from_production_image():
    production = read("Dockerfile")
    test_image = read("Dockerfile.test")
    test_script = read("test.sh")

    assert 'pip install .' in production
    assert 'pip install ".[test]"' not in production
    assert 'pip install ".[test]"' in test_image
    assert "COPY tests ./tests" in test_image
    assert "Dockerfile.test" in test_script
    assert 'docker run --rm "$image" pytest -q "$@"' in test_script


def test_startup_scripts_are_installed_with_the_application():
    pyproject = read("pyproject.toml")

    assert 'include = ["app*", "scripts*"]' in pyproject
    assert (ROOT / "scripts" / "__init__.py").is_file()


def test_runtime_alembic_files_resolve_outside_the_installed_wheel():
    project_root = resolve_project_root()
    config = build_alembic_config("sqlite+aiosqlite:////tmp/migration-contract.db")

    assert project_root == ROOT
    assert Path(config.config_file_name).resolve() == ROOT / "alembic.ini"
    assert Path(config.get_main_option("script_location")).resolve() == ROOT / "migrations"


def test_compose_persists_documents_backups_and_telegram_fsm():
    local_compose = read("docker-compose.yml")
    timeweb_compose = read("docker-compose.timeweb.yml")

    for source in (local_compose, timeweb_compose):
        assert "redis:7.4-alpine" in source
        assert "--appendonly" in source
        assert "condition: service_healthy" in source
        assert "concierge_redis:/data" in source
        for mount in ("/app/data", "/app/storage", "/app/backups", "/app/logs"):
            assert mount in source
        assert "stop_grace_period: 45s" in source
    assert "DATABASE_URL:" not in local_compose
    assert "0.0.0.0" not in timeweb_compose


def test_production_template_is_fail_closed_and_restart_safe():
    template = read(".env.production.example")

    assert "APP_ENV=production" in template
    assert "RUN_BOT=true" in template
    assert "RUN_SCHEDULER=true" in template
    assert "BOOTSTRAP_DEMO_DATA=false" in template
    assert "ALLOW_TOKEN_QUERY=false" in template
    assert "ENABLE_RECOVERY_ACTIONS=false" in template
    assert "REQUIRE_POSTGRES_IN_PRODUCTION=true" in template
    assert "STARTUP_BACKUP_ENABLED=true" in template
    assert "DATABASE_STARTUP_WAIT_SECONDS=120" in template
    assert "FSM_STORAGE_BACKEND=redis" in template
    assert "REDIS_URL=redis://redis:6379/0" in template
    assert "PAYMENT_PROVIDER=yookassa" in template
    assert "APP_BIND_ADDRESS=127.0.0.1" in template


def test_demo_lawyer_bootstrap_is_explicit():
    source = read("scripts/init_db.py")

    assert "if settings.bootstrap_demo_data:" in source
    assert source.index("if settings.bootstrap_demo_data:") < source.index(
        "await get_or_create_lawyer(db)"
    )
    assert "if settings.bootstrap_admin:" in source


def test_settings_include_container_telegram_and_fsm_restart_policy():
    settings = Settings(_env_file=None)

    assert settings.bootstrap_demo_data is False
    assert settings.bootstrap_admin is True
    assert settings.startup_backup_enabled is True
    assert settings.database_startup_wait_seconds == 120
    assert settings.telegram_drop_pending_updates is False
    assert settings.telegram_singleton_wait_seconds == 120
    assert settings.telegram_singleton_retry_seconds == 3
    assert settings.fsm_storage_backend == "memory"
    assert settings.redis_startup_wait_seconds == 60


def test_bot_rejects_memory_fsm_in_production():
    source = read("app/bot/bot.py")

    assert "RedisStorage.from_url(settings.redis_url)" in source
    assert 'backend == "memory"' in source
    assert 'settings.app_env.strip().lower() != "production"' in source
    assert "Production Telegram FSM должен использовать" in source
    assert "raise PollingExitedError" in source
    assert source.index("try:\n        dispatcher = build_dispatcher()") < source.index(
        "finally:\n        await lease.release()"
    )


def test_production_preflight_requires_runtime_dependencies_and_real_payments():
    source = read("scripts/production_preflight.py")

    assert '"database_wait_valid"' in source
    assert '"fsm_storage_is_redis"' in source
    assert '"redis_url_ready"' in source
    assert '"trusted_proxy_configured"' in source
    assert 'settings.payment_provider == "yookassa"' in source
    assert "secrets_exposed" in source
    assert "your-domain" in source
    assert 'not in {"host", "localhost"}' in source


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


def test_placeholder_endpoints_are_rejected():
    assert not production_preflight._postgres_url_ready(
        "postgresql+asyncpg://USER:PASSWORD@HOST:PORT/DATABASE"
    )
    assert production_preflight._postgres_url_ready(
        "postgresql+asyncpg://dlc:strong%21pass@10.10.0.5:5432/legal_concierge"
    )
    assert not production_preflight._public_url_ready("https://YOUR-DOMAIN")
    assert production_preflight._public_url_ready("https://concierge.example.org")
    assert production_preflight._redis_url_ready("redis://redis:6379/0")


@pytest.mark.asyncio
async def test_sqlite_telegram_polling_lease_is_singleton_and_reusable(tmp_path):
    first = TelegramPollingLease(lock_dir=tmp_path, dialect_name="sqlite")
    second = TelegramPollingLease(lock_dir=tmp_path, dialect_name="sqlite")

    assert await first.acquire_once() is True
    assert await second.acquire_once() is False

    await first.release()
    assert await second.acquire_once() is True
    await second.release()
