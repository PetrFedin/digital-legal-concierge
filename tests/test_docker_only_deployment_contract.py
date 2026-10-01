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
    assert "COPY .github/workflows ./.github/workflows" in test_image
    assert "Dockerfile.test" in test_script
    assert "python scripts/init_db.py" in test_script
    assert 'exec pytest -q "$@"' in test_script


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
    assert "PAYMENT_PROVIDER=offline" in template
    assert "RUNTIME_ROLE=all" in template
    assert "BOT_DATABASE_URL=" in template
    assert "BOT_REDIS_URL=redis://127.0.0.1:6379/0" in template
    assert "TELEGRAM_API_IPV6=" in template
    assert "TELEGRAM_EXPECTED_USERNAME=DL_Concierge_bot" in template
    assert "DOCUMENT_MALWARE_SCANNER=clamav" in template
    assert "CLAMAV_HOST=clamav" in template
    assert "CLAMAV_PORT=3310" in template
    assert "CLAMAV_TIMEOUT_SECONDS=15" in template
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
    assert settings.runtime_role == "all"


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


def test_production_preflight_requires_runtime_dependencies_and_valid_payment_mode():
    source = read("scripts/production_preflight.py")

    assert '"database_wait_valid"' in source
    assert '"fsm_storage_is_redis"' in source
    assert '"redis_url_ready"' in source
    assert '"trusted_proxy_configured"' in source
    assert 'payment_provider == "offline"' in source
    assert 'payment_provider == "yookassa"' in source
    assert '"payment_provider_ready": payment_ready' in source
    assert '"runtime_role_valid"' in source
    assert '"bot_mode_matches_runtime_role"' in source
    assert '"scheduler_mode_matches_runtime_role"' in source
    assert "Оплата работает в офлайн-режиме" in source
    assert "secrets_exposed" in source
    assert "your-domain" in source
    assert 'not in {"host", "localhost"}' in source


def _configure_preflight_paths(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(production_preflight.settings, "app_env", "production")
    monkeypatch.setattr(
        production_preflight.settings,
        "document_malware_scanner",
        "clamav",
    )
    monkeypatch.setattr(production_preflight.settings, "clamav_host", "clamav")
    monkeypatch.setattr(production_preflight.settings, "clamav_port", 3310)
    monkeypatch.setattr(production_preflight.settings, "clamav_timeout_seconds", 15)
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


def test_timeweb_production_compose_keeps_clamd_private_and_split_bot_loopback_only():
    timeweb = read("docker-compose.timeweb.yml")
    split = read("docker-compose.timeweb.split.yml")

    assert "clamav/clamav:1.5.4-debian" in timeweb
    assert "concierge_clamav:/var/lib/clamav" in timeweb
    assert "clamav:\n        condition: service_healthy" in timeweb
    assert '127.0.0.1:3310:3310' not in timeweb

    assert '127.0.0.1:3310:3310' in split
    assert "CLAMAV_HOST: clamav" in split
    assert "CLAMAV_HOST: 127.0.0.1" in split


def test_production_preflight_requires_fail_closed_malware_scanner(monkeypatch, tmp_path):
    _configure_preflight_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(production_preflight.settings, "document_malware_scanner", "disabled")

    report = production_preflight.build_report()

    assert report["checks"]["document_malware_scanner_is_clamav"] is False
    assert "document_malware_scanner_is_clamav" in report["failed"]


def test_production_preflight_accepts_offline_but_rejects_disabled_and_fake(
    monkeypatch,
    tmp_path,
):
    _configure_preflight_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(production_preflight.settings, "runtime_role", "all")
    monkeypatch.setattr(production_preflight.settings, "run_bot", True)
    monkeypatch.setattr(production_preflight.settings, "run_scheduler", True)

    monkeypatch.setattr(production_preflight.settings, "payment_provider", "offline")
    offline_report = production_preflight.build_report()
    assert offline_report["checks"]["payment_provider_ready"] is True
    assert any(
        "офлайн-режиме" in warning
        for warning in offline_report["warnings"]
    )

    monkeypatch.setattr(production_preflight.settings, "payment_provider", "disabled")
    disabled_report = production_preflight.build_report()
    assert disabled_report["checks"]["payment_provider_ready"] is False
    assert "payment_provider_ready" in disabled_report["failed"]
    assert any(
        "fail-closed" in warning
        for warning in disabled_report["warnings"]
    )

    monkeypatch.setattr(production_preflight.settings, "payment_provider", "fake")
    fake_report = production_preflight.build_report()

    assert fake_report["checks"]["payment_provider_ready"] is False
    assert "payment_provider_ready" in fake_report["failed"]


def test_production_preflight_requires_complete_yookassa_credentials(
    monkeypatch,
    tmp_path,
):
    _configure_preflight_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(production_preflight.settings, "payment_provider", "yookassa")
    monkeypatch.setattr(production_preflight.settings, "yookassa_shop_id", "")
    monkeypatch.setattr(production_preflight.settings, "yookassa_secret_key", "")

    incomplete_report = production_preflight.build_report()
    assert incomplete_report["checks"]["payment_provider_ready"] is False

    monkeypatch.setattr(
        production_preflight.settings,
        "yookassa_shop_id",
        "production-shop",
    )
    monkeypatch.setattr(
        production_preflight.settings,
        "yookassa_secret_key",
        "production-secret-key",
    )

    complete_report = production_preflight.build_report()
    assert complete_report["checks"]["payment_provider_ready"] is True


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



def test_production_preflight_accepts_split_web_and_bot_runtime_roles(
    monkeypatch,
    tmp_path,
):
    _configure_preflight_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(production_preflight.settings, "payment_provider", "offline")

    monkeypatch.setattr(production_preflight.settings, "runtime_role", "web")
    monkeypatch.setattr(production_preflight.settings, "run_bot", False)
    monkeypatch.setattr(production_preflight.settings, "run_scheduler", False)
    web_report = production_preflight.build_report()
    assert web_report["checks"]["runtime_role_valid"] is True
    assert web_report["checks"]["bot_mode_matches_runtime_role"] is True
    assert web_report["checks"]["scheduler_mode_matches_runtime_role"] is True

    monkeypatch.setattr(production_preflight.settings, "runtime_role", "bot")
    monkeypatch.setattr(production_preflight.settings, "run_bot", True)
    monkeypatch.setattr(production_preflight.settings, "run_scheduler", True)
    bot_report = production_preflight.build_report()
    assert bot_report["checks"]["runtime_role_valid"] is True
    assert bot_report["checks"]["bot_mode_matches_runtime_role"] is True
    assert bot_report["checks"]["scheduler_mode_matches_runtime_role"] is True

    monkeypatch.setattr(production_preflight.settings, "run_scheduler", False)
    invalid_report = production_preflight.build_report()
    assert invalid_report["checks"]["scheduler_mode_matches_runtime_role"] is False
    assert "scheduler_mode_matches_runtime_role" in invalid_report["failed"]


def test_timeweb_split_compose_declares_host_network_bot_and_proxy_web():
    split = read("docker-compose.timeweb.split.yml")
    deploy = read("timeweb-deploy.sh")
    probe = read("scripts/telegram_worker_probe.py")
    worker = read("app/bot/worker.py")

    assert "container_name: legal-concierge" in split
    assert "container_name: legal-concierge-bot" in split
    assert "RUNTIME_ROLE: web" in split
    assert "RUNTIME_ROLE: bot" in split
    assert "network_mode: host" in split
    assert "api.telegram.org" in split
    assert "BOT_DATABASE_URL" in split
    assert "BOT_REDIS_URL" in split
    assert "proxy:" in split
    assert "docker-compose.timeweb.split.yml" in deploy
    assert "POSTGRES_TELEGRAM_LOCK_KEY" in probe
    assert "polling_lease_held" in probe
    assert "wait_for_database" in worker
    assert "wait_for_redis" in worker
    assert "NotificationDispatcher" in worker
    assert "AppScheduler" in worker

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
