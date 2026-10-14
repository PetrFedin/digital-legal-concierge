"""Local source preflight, never a substitute for customer/live acceptance.

Historical versions silently created users/cases, migrated the configured DB
and simulated payments in the default path. The default is now read-only with
respect to business data. Synthetic writes require an explicit, isolated test
database and can never be presented as production/provider UAT evidence.
"""
from __future__ import annotations

import argparse
import compileall
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]

STATIC_CHECKS = (
    ("source file layout", (sys.executable, "scripts/production_acceptance.py")),
    ("local launch configuration", (sys.executable, "scripts/launch_check.py")),
)

SYNTHETIC_WRITES = (
    ("initialise disposable test database", (sys.executable, "scripts/init_db.py")),
    ("operator metadata check", (sys.executable, "scripts/operator_check.py")),
    ("synthetic M1/M2 smoke", (sys.executable, "scripts/e2e_smoke.py")),
    ("synthetic M1/M2 scenario", (sys.executable, "scripts/e2e_m1_m2_full.py")),
)


def run(title: str, cmd: tuple[str, ...]) -> bool:
    print(f"\n=== {title} ===")
    result = subprocess.run(list(cmd), cwd=ROOT, text=True, check=False)
    passed = result.returncode == 0
    print("OK" if passed else "FAIL")
    return passed


def synthetic_environment_errors(env: Mapping[str, str]) -> list[str]:
    """Fail closed unless an explicitly opted-in empty temporary SQLite DB is used."""
    errors: list[str] = []
    if env.get("DLC_ALLOW_DISPOSABLE_E2E") != "YES":
        errors.append("DLC_ALLOW_DISPOSABLE_E2E=YES обязателен")
    if env.get("APP_ENV") != "test":
        errors.append("APP_ENV=test обязателен")
    if env.get("RUN_BOT", "").lower() != "false":
        errors.append("RUN_BOT=false обязателен")
    if env.get("RUN_SCHEDULER", "").lower() != "false":
        errors.append("RUN_SCHEDULER=false обязателен")
    if env.get("PAYMENT_PROVIDER") != "fake":
        errors.append("PAYMENT_PROVIDER=fake обязателен")

    database_url = env.get("DATABASE_URL", "")
    prefix = "sqlite+aiosqlite:///"
    if not database_url.startswith(prefix):
        errors.append("DATABASE_URL должен ссылаться на отдельный временный SQLite")
    else:
        file_path = Path(database_url[len(prefix):])
        temp_root = Path(tempfile.gettempdir()).resolve()
        if (
            not file_path.is_absolute()
            or file_path.parent.resolve() != temp_root
            or not file_path.name.startswith("dlc-acceptance-")
            or not file_path.name.endswith(".db")
            or len(file_path.stem) <= len("dlc-acceptance-")
            or file_path.exists()
            or file_path.is_symlink()
        ):
            errors.append(
                "DATABASE_URL должен указывать на НЕСУЩЕСТВУЮЩИЙ "
                "временный файл dlc-acceptance-<id>.db"
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safe source verification; not live UAT")
    parser.add_argument(
        "--synthetic-e2e",
        action="store_true",
        help="Dangerous synthetic DB writes; requires dedicated disposable SQLite and explicit opt-in",
    )
    args = parser.parse_args(argv)
    if args.synthetic_e2e:
        errors = synthetic_environment_errors(os.environ)
        if errors:
            print("ОТКАЗАНО: синтетический E2E не допущен.")
            for error in errors:
                print(" -", error)
            return 2

    print("Проверка исходного кода и локальных файлов (НЕ приёмка Telegram/оплат)")
    compiled = compileall.compile_dir(str(ROOT / "app"), quiet=1)
    print("\nCompileall:", "OK" if compiled else "FAIL")
    passed = compiled
    for title, cmd in STATIC_CHECKS:
        passed = run(title, cmd) and passed

    if not args.synthetic_e2e:
        print(
            "\nИтог: SOURCE PREFLIGHT OK" if passed else "\nИтог: SOURCE PREFLIGHT FAILED"
        )
        print("Реальный Telegram, YooKassa, Timeweb, UAT и legal signoff здесь НЕ проверяются.")
        return 0 if passed else 1

    for title, cmd in SYNTHETIC_WRITES:
        passed = run(title, cmd) and passed
    print(
        "\nИтог: SYNTHETIC TEST ONLY — НЕ ПРОДАКШЕН И НЕ ПРИЁМКА"
        if passed else "\nИтог: SYNTHETIC TEST FAILED"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
