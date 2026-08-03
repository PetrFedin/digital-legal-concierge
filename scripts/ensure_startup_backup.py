from __future__ import annotations

import json
from dataclasses import asdict

from app.config import settings
from app.security.backup_freshness import (
    backup_freshness_status,
    clear_backup_freshness_cache,
)
from app.security.backup_service import create_provider_encrypted_backup


def main() -> int:
    production = settings.app_env.strip().lower() == "production"
    required = production and settings.backup_readiness_required_in_production
    if not required or not settings.startup_backup_enabled:
        print(
            json.dumps(
                {
                    "ok": True,
                    "operation": "startup-backup",
                    "created": False,
                    "reason": "not-required-or-disabled",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0

    current = backup_freshness_status()
    if current.ok:
        print(
            json.dumps(
                {
                    "ok": True,
                    "operation": "startup-backup",
                    "created": False,
                    "reason": "fresh-verified-backup-exists",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0

    result = create_provider_encrypted_backup()
    clear_backup_freshness_cache()
    refreshed = backup_freshness_status()
    payload = {
        "ok": bool(refreshed.ok),
        "operation": "startup-backup",
        "created": True,
        "backup": asdict(result),
        "freshness": refreshed.as_dict(),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if refreshed.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
