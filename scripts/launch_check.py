from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / '.env'
REQUIRED_PATHS = [
    'app/main.py',
    'app/bot/bot.py',
    'app/api/admin.py',
    'app/api/payment_webhooks.py',
    'scripts/init_db.py',
    'scripts/e2e_smoke.py',
    'scripts/e2e_m1_m2_full.py',
    'docs/source_specs/UX_UI_спецификация_Telegram_бот.docx',
    'docs/source_specs/Функциональная_спецификация_MVP.docx',
]


def parse_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    data: dict[str, str] = {}
    for raw in ENV_PATH.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        data[key.strip()] = value.strip()
    return data


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(('127.0.0.1', port)) != 0


def main() -> int:
    print('Проверка готовности запуска v19')
    ok = True

    for rel in REQUIRED_PATHS:
        exists = (ROOT / rel).exists()
        print(('OK  ' if exists else 'FAIL') + rel)
        ok = ok and exists

    env = parse_env()
    if not env:
        print('WARN .env не найден. Запустите ./run.sh или python scripts/first_run.py')
        # .env создается автоматически через ./run.sh, поэтому отсутствие файла не блокирует пакетную проверку.
    else:
        bot_token = env.get('BOT_TOKEN', '')
        run_bot = env.get('RUN_BOT', 'false').lower() == 'true'
        if run_bot and (not bot_token or bot_token == 'CHANGE_ME'):
            print('FAIL RUN_BOT=true, но BOT_TOKEN не задан')
            ok = False
        else:
            print('OK  BOT_TOKEN / RUN_BOT')

        admin_token = env.get('ADMIN_API_TOKEN', '')
        if not admin_token or admin_token == 'dev-admin-token':
            print('WARN ADMIN_API_TOKEN пустой или dev-admin-token. Для продакшена замените.')
        else:
            print('OK  ADMIN_API_TOKEN')

        provider = env.get('PAYMENT_PROVIDER', 'fake')
        if provider == 'fake':
            print('WARN PAYMENT_PROVIDER=fake. Подходит для теста, не для приема реальных денег.')
        elif provider == 'yookassa':
            if not env.get('YOOKASSA_SHOP_ID') or not env.get('YOOKASSA_SECRET_KEY'):
                print('FAIL YooKassa выбрана, но ключи не заполнены')
                ok = False
            else:
                print('OK  YooKassa ключи заданы')

    if port_is_free(8000):
        print('OK  порт 8000 свободен')
    else:
        print('WARN порт 8000 занят. Возможно сервис уже запущен.')

    print('\nИтог:', 'можно запускать' if ok else 'нужно исправить замечания')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
