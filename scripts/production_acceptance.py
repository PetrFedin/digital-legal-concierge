from __future__ import annotations

import asyncio
import json

from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.domain.payments.mode import payment_mode_valid, payment_provider_name
from app.release import APPLICATION_VERSION, expected_migration_heads, release_metadata
from app.security.audit_integrity import verify_audit_chain
from app.security.backup_freshness import backup_freshness_status
from app.security.keyring import security_key_status
from scripts.production_preflight import build_report
from scripts.wait_for_database import wait_for_database
from scripts.wait_for_redis import wait_for_redis


async def _database_evidence() -> dict[str, object]:
    async with AsyncSessionLocal() as db:
        versions = set(
            (
                await db.execute(text("SELECT version_num FROM alembic_version"))
            ).scalars().all()
        )
        audit = await verify_audit_chain(db)
        counts = {}
        for table in ("users", "cases", "payments", "audit_logs"):
            value = (
                await db.execute(text(f'SELECT COUNT(*) FROM "{table}"'))
            ).scalar_one()
            counts[table] = int(value)

    expected = set(expected_migration_heads())
    return {
        "migration_heads": sorted(versions),
        "migration_heads_expected": sorted(expected),
        "migration_heads_match": versions == expected,
        "audit_ok": bool(audit.get("ok")),
        "audit_event_count": int(audit.get("event_count") or 0),
        "counts": counts,
    }


async def build_acceptance_report() -> dict[str, object]:
    preflight = build_report()
    database_wait = await wait_for_database()
    redis_wait = await wait_for_redis()
    database = await _database_evidence()
    keys = security_key_status()
    backup = backup_freshness_status()
    release = release_metadata()

    checks = {
        "preflight": bool(preflight.get("ok")),
        "database_reachable": bool(database_wait.get("ok")),
        "redis_reachable": bool(redis_wait.get("ok")),
        "migration_heads_match": bool(database["migration_heads_match"]),
        "audit_chain_valid": bool(database["audit_ok"]),
        "security_keys_ready": bool(keys.get("ok")),
        "verified_backup_fresh": bool(backup.ok),
        "payment_mode_valid": bool(payment_mode_valid()),
        "release_identity_present": (
            release.get("git_commit") not in {None, "", "unknown"}
            and release.get("image_tag") not in {None, "", "unknown"}
        ),
    }

    return {
        "ok": all(checks.values()),
        "application_version": APPLICATION_VERSION,
        "payment_mode": payment_provider_name(),
        "runtime_role": preflight.get("environment") and __import__(
            "app.config", fromlist=["settings"]
        ).settings.runtime_role,
        "checks": checks,
        "database": database,
        "release": {
            "application_version": release.get("application_version"),
            "release": release.get("release"),
            "git_commit": release.get("git_commit"),
            "image_repository": release.get("image_repository"),
            "image_tag": release.get("image_tag"),
            "migration_heads": release.get("migration_heads"),
        },
        "preflight_failed": list(preflight.get("failed") or []),
        "preflight_warnings": list(preflight.get("warnings") or []),
        "secrets_exposed": False,
    }


def main() -> int:
    report = asyncio.run(build_acceptance_report())
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
