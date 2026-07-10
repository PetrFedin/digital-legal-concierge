from __future__ import annotations

import argparse
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"


def read_env(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def write_env(values: dict[str, str]) -> None:
    order = [
        "APP_ENV",
        "DATABASE_URL",
        "BOT_TOKEN",
        "RUN_BOT",
        "RUN_SCHEDULER",
        "ADMIN_API_TOKEN",
        "LEGAL_KEY_RATE",
        "STORAGE_DIR",
        "PAYMENT_WEBHOOK_SECRET",
        "PUBLIC_BASE_URL",
    ]
    lines = ["# Digital Legal Concierge Telegram Bot", "# Файл создан scripts/setup_env.py", ""]
    for key in order:
        if key in values:
            lines.append(f"{key}={values[key]}")
    for key, value in values.items():
        if key not in order:
            lines.append(f"{key}={value}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Создать или обновить .env для Telegram-бота")
    parser.add_argument("--bot-token", default=None, help="Токен Telegram-бота от BotFather")
    parser.add_argument("--admin-token", default=None, help="Токен доступа к админке/API")
    parser.add_argument("--public-url", default=None, help="Публичный URL сервера, если есть")
    parser.add_argument("--no-bot", action="store_true", help="Запускать только backend без polling Telegram")
    args = parser.parse_args()

    values = read_env(EXAMPLE)
    values.update(read_env(ENV))

    values.setdefault("APP_ENV", "local")
    values.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./legal_bot.db")
    values.setdefault("RUN_BOT", "true")
    values.setdefault("RUN_SCHEDULER", "true")
    values.setdefault("LEGAL_KEY_RATE", "0.16")
    values.setdefault("STORAGE_DIR", "./storage")
    values.setdefault("PUBLIC_BASE_URL", "http://localhost:8000")

    if args.bot_token:
        values["BOT_TOKEN"] = args.bot_token
    elif values.get("BOT_TOKEN") in (None, "", "CHANGE_ME"):
        values["BOT_TOKEN"] = "CHANGE_ME"

    if args.no_bot:
        values["RUN_BOT"] = "false"

    if args.admin_token:
        values["ADMIN_API_TOKEN"] = args.admin_token
    elif values.get("ADMIN_API_TOKEN") in (None, "", "dev-admin-token", "change-me"):
        values["ADMIN_API_TOKEN"] = "admin-" + secrets.token_urlsafe(18)

    if values.get("PAYMENT_WEBHOOK_SECRET") in (None, "", "change-this-payment-secret", "dev-payment-secret"):
        values["PAYMENT_WEBHOOK_SECRET"] = "pay-" + secrets.token_urlsafe(24)

    if args.public_url:
        values["PUBLIC_BASE_URL"] = args.public_url.rstrip("/")

    write_env(values)
    print(".env готов")
    print(f"ADMIN_API_TOKEN={values['ADMIN_API_TOKEN']}")
    if values.get("BOT_TOKEN") == "CHANGE_ME" and values.get("RUN_BOT") == "true":
        print("Внимание: BOT_TOKEN пока не задан. Вставьте токен от BotFather в .env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
