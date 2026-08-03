from __future__ import annotations

import json
import re
from pathlib import Path

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
        postgres = database_url.startswith("postgresql+asyncpg://")
        persistent_sqlite = database_url.startswith(
            "sqlite+aiosqlite:////app/data/"
        )
        payment_ready = (
            settings.payment_provider == "yookassa"
            and bool(settings.yookassa_shop_id)
            and _secret_ready(settings.yookassa_secret_key, minimum=8)
        )
        checks.update(
            {
                "bot_enabled": bool(settings.run_bot),
                "scheduler_enabled": bool(settings.run_scheduler),
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
                "public_base_url_https": str(settings.public_base_url)
                .lower()
                .startswith("https://"),
                "trusted_proxy_configured": bool(
                    str(settings.trusted_proxy_cidrs or "").strip()
                ),
                "database_is_persistent": postgres or persistent_sqlite,
                "postgres_requirement_satisfied": (
                    postgres if settings.require_postgres_in_production else True
                ),
                "fsm_storage_is_redis": settings.fsm_storage_backend == "redis",
                "redis_url_ready": redis_url.startswith(("redis://", "rediss://")),
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
