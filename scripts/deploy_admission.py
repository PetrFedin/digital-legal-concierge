"""Pre-deployment admission check: exact release approval is not health readiness.

Runs in the candidate app image, before switching containers. The current
production gate is intentionally HOLD pending independent legal and customer
acceptance. This script neither contacts external services nor modifies data.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.config import settings
from app.domain.release_admission import runtime_release_admission


NONPRODUCTION_ENVIRONMENTS = frozenset({"local", "development", "test", "staging"})


def evaluate_deployment_admission(
    environment: str,
    admission: Mapping[str, Any],
) -> dict[str, Any]:
    env = str(environment or "").strip().lower()
    approval = (
        admission.get("status") == "ACCEPTED"
        and admission.get("release_authorized") is True
        and admission.get("customer_accepted") is True
    )
    if env == "production":
        return {
            "ok": approval,
            "status": "PRODUCTION_APPROVED" if approval else "PRODUCTION_HOLD",
            "environment": "production",
            "message": (
                "Подтверждённый допуск к production."
                if approval else
                "Юридическая, клиентская или внешняя приёмка не подтверждена."
            ),
        }
    if env in NONPRODUCTION_ENVIRONMENTS:
        return {
            "ok": True,
            "status": "NONPRODUCTION_ONLY",
            "environment": env,
            "message": "Допущен только локальный или изолированный тестовый запуск.",
        }
    return {
        "ok": False,
        "status": "UNKNOWN_ENVIRONMENT",
        "environment": "unknown",
        "message": "Неподтверждённое APP_ENV: развертывание запрещено.",
    }


def main() -> int:
    report = evaluate_deployment_admission(
        settings.app_env, runtime_release_admission()
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
