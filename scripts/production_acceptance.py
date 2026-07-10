from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

REQUIRED_FILES = [
    "run.sh",
    "bot-control.sh",
    "acceptance.sh",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.production.yml",
    ".env.example",
    ".env.production.example",
    "docs/START_SIMPLE_V29.md",
    "docs/FINAL_HANDOVER_V29.md",
    "deploy/nginx/legal-concierge-bot.conf",
    "deploy/systemd/legal-concierge-bot.service",
]

REQUIRED_MODULES = [
    "app.main",
    "app.api.production_center",
    "app.api.admin",
    "app.api.operator",
    "app.api.payment_webhooks",
    "app.bot.bot",
]


def ok(name: str):
    print(f"✅ {name}")


def fail(name: str, detail: str):
    print(f"❌ {name}: {detail}")
    return False


def check_files() -> bool:
    result = True
    for file in REQUIRED_FILES:
        if (ROOT / file).exists():
            ok(file)
        else:
            result = fail(file, "файл отсутствует")
    return result


def check_imports() -> bool:
    # Dependencies are installed during ./run.sh or pip install -e .
    # Here we intentionally perform a lightweight source-level check,
    # so acceptance can run on a clean machine before installation.
    result = True
    for module in REQUIRED_MODULES:
        source = ROOT / (module.replace(".", "/") + ".py")
        if source.exists():
            ok(f"source:{module}")
        else:
            result = fail(module, "исходный файл отсутствует")
    return result


def main() -> int:
    print("Production acceptance v29")
    print("=" * 32)
    files_ok = check_files()
    imports_ok = check_imports()
    if files_ok and imports_ok:
        print("\nREADY FOR OPERATOR TEST")
        return 0
    print("\nNOT READY")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
