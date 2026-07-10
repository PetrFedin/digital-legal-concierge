from __future__ import annotations

import compileall
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(title: str, cmd: list[str]) -> bool:
    print(f"\n=== {title} ===")
    result = subprocess.run(cmd, cwd=ROOT, text=True)
    if result.returncode == 0:
        print(f"OK: {title}")
        return True
    print(f"FAIL: {title}")
    return False


def main() -> int:
    print("Полная проверка Telegram-бота v14")
    ok = True
    print("\n=== compile app ===")
    ok = compileall.compile_dir(str(ROOT / "app"), quiet=1) and ok
    print("OK" if ok else "FAIL")
    for title, cmd in [
        ("init_db", [sys.executable, "scripts/init_db.py"]),
        ("launch_check", [sys.executable, "scripts/launch_check.py"]),
        ("operator_check", [sys.executable, "scripts/operator_check.py"]),
        ("e2e_smoke", [sys.executable, "scripts/e2e_smoke.py"]),
        ("e2e_m1_m2_full", [sys.executable, "scripts/e2e_m1_m2_full.py"]),
        ("final_acceptance", [sys.executable, "scripts/final_acceptance.py"]),
    ]:
        ok = run(title, cmd) and ok
    print("\nИтог v14:", "ГОТОВО К ЗАПУСКУ" if ok else "НУЖНО ИСПРАВИТЬ ОШИБКИ")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
