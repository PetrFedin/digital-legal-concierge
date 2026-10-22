"""Pre-deployment authority: compose intent AND runtime environment must agree.

A health endpoint, a git SHA, a provider secret, or a manually supplied
environment variable is not a signed release decision. Production remains
HOLD until independently validated legal and customer acceptance exists.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from app.config import settings
from app.domain.release_admission import runtime_release_admission


def evaluate_deployment_admission(
    environment: str,
    admission: Mapping[str, Any],
    *,
    target: str,
) -> dict[str, Any]:
    env = str(environment or "").strip().lower()
    target = str(target or "").strip().lower()
    if target not in {"local", "staging", "production"}:
        return {
            "ok": False,
            "status": "UNKNOWN_TARGET",
            "target": "unknown",
            "message": "Неизвестное назначение развертывания.",
        }

    accepted = (
        admission.get("status") == "ACCEPTED"
        and admission.get("release_authorized") is True
        and admission.get("customer_accepted") is True
    )
    if target == "production":
        allowed = env == "production" and accepted
        return {
            "ok": allowed,
            "status": "PRODUCTION_APPROVED" if allowed else "PRODUCTION_HOLD",
            "target": "production",
            "message": (
                "Подтверждённый production-допуск."
                if allowed else
                "Production запрещён без совпадающего окружения и подписанной приёмки."
            ),
        }
    if env == "production":
        return {
            "ok": False,
            "status": "PRODUCTION_HOLD",
            "target": target,
            "message": "Нельзя выдавать production-окружение за тестовую среду.",
        }

    if target == "staging" and env in {"test", "staging"}:
        return {
            "ok": True,
            "status": "NONPRODUCTION_ONLY",
            "target": "staging",
            "message": "Разрешён только явно выделенный тестовый стенд.",
        }
    if target == "local" and env in {"local", "development", "test"}:
        return {
            "ok": True,
            "status": "NONPRODUCTION_ONLY",
            "target": "local",
            "message": "Разрешён только локальный тестовый запуск.",
        }

    return {
        "ok": False,
        "status": "ENVIRONMENT_TARGET_MISMATCH",
        "target": target,
        "message": "Назначение и APP_ENV не совпадают. Развертывание запрещено.",
    }


def main() -> int:
    report = evaluate_deployment_admission(
        settings.app_env,
        runtime_release_admission(),
        target=os.getenv("DLC_DEPLOY_TARGET", ""),
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
