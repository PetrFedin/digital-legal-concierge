from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHECKS = [
    ("app.main", "FastAPI application"),
    ("app.bot.bot", "Telegram bot"),
    ("app.api.admin", "Admin API"),
    ("app.api.exports", "CSV export API"),
    ("app.api.payment_webhooks", "Payment webhooks"),
]


def ok(name: str) -> None:
    print(f"[OK] {name}")


def fail(name: str, msg: str) -> int:
    print(f"[FAIL] {name}: {msg}")
    return 1


def run_py_compile() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "app", "scripts"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        return fail("compileall", "ошибка компиляции")
    ok("compileall")
    return 0


def main() -> int:
    os.chdir(ROOT)
    errors = run_py_compile()
    for module_name, label in CHECKS:
        try:
            importlib.import_module(module_name)
            ok(label)
        except Exception as exc:
            errors += fail(label, repr(exc))
    required_paths = ["run.sh", "bot-control.sh", ".env.example", "docs/START_SIMPLE_V15.md"]
    for rel in required_paths:
        path = ROOT / rel
        if path.exists():
            ok(rel)
        else:
            errors += fail(rel, "файл не найден")
    if errors:
        print("\nИтог: есть ошибки.")
        return 1
    print("\nИтог: v19 готов к запуску.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
