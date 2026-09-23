from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from app.config import settings


PLACEHOLDERS = {
    "",
    "CHANGE_ME",
    "dev-admin-token",
    "dev-payment-secret",
    "change-this-payment-secret",
}
BOT_TOKEN_PATTERN = re.compile(r"^[0-9]{6,15}:[A-Za-z0-9_-]{30,}$")


def _secret_ready(value: object, *, minimum: int = 32) -> bool:
    normalized = str(value or "")
    return len(normalized) >= minimum and normalized not in PLACEHOLDERS


def _directory_ready(value: str) -> bool:
    path = Path(value)
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".dlc-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _postgres_url_ready(value: str) -> bool:
    try:
        parsed = urlparse(value)
        database_name = parsed.path.strip("/").lower()
        _ = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "postgresql+asyncpg"
        and parsed.hostname
        and parsed.hostname.lower() not in {"host", "localhost"}
        and parsed.username
        and parsed.username.lower() not in {"user", "username"}
        and parsed.password
        and parsed.password.lower() not in {"password", "change_me", "changeme"}
        and database_name
        and database_name not in {"database", "dbname"}
    )


def _redis_url_ready(value: str) -> bool:
    try:
        parsed = urlparse(value)
        _ = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"redis", "rediss"}
        and parsed.hostname
        and parsed.path.strip("/").isdigit()
    )


def _public_url_ready(value: str) -> bool:
    try:
        parsed = urlparse(value)
        hostname = str(parsed.hostname or "").lower()
        _ = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and hostname
        and "." in hostname
        and hostname not in {"localhost", "example.com"}
        and "your-domain" not in hostname
    )


def build_report() -> dict[str, object]:
    production = settings.app_env.strip().lower() == "production"
    checks: dict[str, bool] = {
        "storage_directory_writable": _directory_ready(settings.storage_dir),
        "backup_directory_writable": _directory_ready(settings.backup_dir),
    }
    warnings: list[str] = []

    if production:
        key_values = [
            settings.session_signing_key,
            settings.security_hmac_key,
            settings.mfa_encryption_key,
            settings.audit_integrity_key,
            settings.document_encryption_key,
            settings.backup_encryption_key,
        ]
        database_url = str(settings.database_url or "")
        redis_url = str(settings.redis_url or "")
        postgres = _postgres_url_ready(database_url)
        persistent_sqlite = database_url.startswith(
            "sqlite+aiosqlite:////app/data/"
        )
        payment_provider = settings.payment_provider.strip().lower()
        payment_ready = payment_provider in {"disabled", "offline"} or (
            payment_provider == "yookassa"
            and bool(settings.yookassa_shop_id)
            and _secret_ready(settings.yookassa_secret_key, minimum=8)
        )
        runtime_role = str(settings.runtime_role or "all").strip().lower()
        role_valid = runtime_role in {"all", "web", "bot"}
        bot_expected = runtime_role in {"all", "bot"}
        scheduler_expected = runtime_role in {"all", "web"}
        checks.update(
            {
                "runtime_role_valid": role_valid,
                "bot_mode_matches_runtime_role": (
                    bool(settings.run_bot) == bot_expected if role_valid else False
                ),
                "scheduler_mode_matches_runtime_role": (
                    bool(settings.run_scheduler) == scheduler_expected
                    if role_valid
                    else False
                ),
                "bot_token_ready": bool(
                    BOT_TOKEN_PATTERN.fullmatch(str(settings.bot_token or ""))
                ),
                "admin_api_token_ready": _secret_ready(settings.admin_api_token),
                "admin_password_ready": _secret_ready(
                    settings.admin_password,
                    minimum=16,
                ),
                "admin_password_is_separate": bool(
                    settings.admin_password
                    and settings.admin_password != settings.admin_api_token
                ),
                "security_keys_ready": all(
                    _secret_ready(value) for value in key_values
                ),
                "security_keys_are_unique": len(set(key_values)) == len(key_values),
                "payment_webhook_secret_ready": _secret_ready(
                    settings.payment_webhook_secret
                ),
                "public_base_url_ready": _public_url_ready(
                    str(settings.public_base_url or "")
                ),
                "trusted_proxy_configured": bool(
                    str(settings.trusted_proxy_cidrs or "").strip()
                ),
                "database_is_persistent": postgres or persistent_sqlite,
                "postgres_requirement_satisfied": (
                    postgres if settings.require_postgres_in_production else True
                ),
                "database_wait_valid": 10
                <= int(settings.database_startup_wait_seconds)
                <= 600,
                "fsm_storage_is_redis": (
                    settings.fsm_storage_backend.strip().lower() == "redis"
                ),
                "redis_url_ready": _redis_url_ready(redis_url),
                "redis_wait_valid": 10
                <= int(settings.redis_startup_wait_seconds)
                <= 300,
                "token_query_disabled": not settings.allow_token_query,
                "demo_mode_disabled": not settings.demo_mode,
                "demo_bootstrap_disabled": not settings.bootstrap_demo_data,
                "legacy_key_fallback_disabled": (
                    not settings.allow_legacy_security_key_fallback
                ),
                "recovery_actions_disabled": not settings.enable_recovery_actions,
                "payment_provider_ready": payment_ready,
                "retention_is_dry_run": bool(settings.case_retention_dry_run),
                "telegram_wait_valid": 10
                <= int(settings.telegram_singleton_wait_seconds)
                <= 600,
                "telegram_retry_valid": 1
                <= int(settings.telegram_singleton_retry_seconds)
                <= 30,
            }
        )
        if payment_provider == "offline":
            warnings.append(
                "Оплата работает в офлайн-режиме: обязательства сохраняются, "
                "поступление подтверждает администратор после независимой сверки"
            )
        elif payment_provider == "disabled":
            warnings.append(
                "Платёжный контур отключён fail-closed: внешние ссылки и "
                "production no-payment bypass недоступны"
            )
        if persistent_sqlite:
            warnings.append(
                "Production использует SQLite: разрешён только один экземпляр приложения"
            )
    else:
        warnings.append("APP_ENV не production: строгие production-проверки пропущены")

    failed = sorted(name for name, ok in checks.items() if not ok)
    return {
        "ok": not failed,
        "environment": settings.app_env,
        "checks": checks,
        "failed": failed,
        "warnings": warnings,
        "secrets_exposed": False,
    }


def main() -> int:
    report = build_report()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
