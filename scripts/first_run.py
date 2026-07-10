from __future__ import annotations

import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / '.env'
EXAMPLE_PATH = ROOT / '.env.example'


def ask(prompt: str, default: str | None = None, secret: bool = False) -> str:
    suffix = f' [{default}]' if default else ''
    value = input(f'{prompt}{suffix}: ').strip()
    return value or (default or '')


def parse_env_text(text: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        data[k.strip()] = v.strip()
    return data


def render_env(data: dict[str, str]) -> str:
    order = [
        'APP_ENV', 'DATABASE_URL', 'BOT_TOKEN', 'RUN_BOT', 'RUN_SCHEDULER',
        'ADMIN_API_TOKEN', 'LEGAL_KEY_RATE', 'STORAGE_DIR', 'PAYMENT_WEBHOOK_SECRET',
        'PUBLIC_BASE_URL', 'PAYMENT_PROVIDER', 'YOOKASSA_SHOP_ID', 'YOOKASSA_SECRET_KEY', 'ADMIN_USERNAME', 'ADMIN_PASSWORD', 'ADMIN_SESSION_COOKIE', 'ALLOW_TOKEN_QUERY',
    ]
    lines = []
    for key in order:
        if key in data:
            lines.append(f'{key}={data[key]}')
    for key in sorted(set(data) - set(order)):
        lines.append(f'{key}={data[key]}')
    return '\n'.join(lines) + '\n'


def main() -> int:
    base = parse_env_text(EXAMPLE_PATH.read_text(encoding='utf-8')) if EXAMPLE_PATH.exists() else {}
    if ENV_PATH.exists():
        print('Найден .env — обновляю только ключевые поля, старые значения сохраню.')
        base.update(parse_env_text(ENV_PATH.read_text(encoding='utf-8')))

    print('\nПервичная настройка Telegram-бота')
    print('Можно нажимать Enter и оставить dev-режим. Для реального бота вставьте токен BotFather.\n')

    base['APP_ENV'] = ask('Окружение', base.get('APP_ENV', 'local'))
    base['BOT_TOKEN'] = ask('BOT_TOKEN от BotFather', base.get('BOT_TOKEN', 'CHANGE_ME'))
    run_bot_default = 'true' if base.get('BOT_TOKEN') and base.get('BOT_TOKEN') != 'CHANGE_ME' else base.get('RUN_BOT', 'false')
    base['RUN_BOT'] = ask('Запускать Telegram-бота сейчас? true/false', run_bot_default).lower()
    base['RUN_SCHEDULER'] = ask('Запускать фоновые задачи? true/false', base.get('RUN_SCHEDULER', 'true')).lower()
    base['PUBLIC_BASE_URL'] = ask('Публичный URL сервиса', base.get('PUBLIC_BASE_URL', 'http://localhost:8000'))

    if base.get('ADMIN_API_TOKEN') in (None, '', 'dev-admin-token'):
        base['ADMIN_API_TOKEN'] = secrets.token_urlsafe(24)
        print('ADMIN_API_TOKEN сгенерирован автоматически.')
    else:
        base['ADMIN_API_TOKEN'] = ask('ADMIN_API_TOKEN', base['ADMIN_API_TOKEN'])

    if base.get('PAYMENT_WEBHOOK_SECRET') in (None, '', 'dev-payment-secret'):
        base['PAYMENT_WEBHOOK_SECRET'] = secrets.token_urlsafe(32)
        print('PAYMENT_WEBHOOK_SECRET сгенерирован автоматически.')


    base['ADMIN_USERNAME'] = ask('Логин администратора', base.get('ADMIN_USERNAME', 'admin'))
    if base.get('ADMIN_PASSWORD') in (None, ''):
        base['ADMIN_PASSWORD'] = secrets.token_urlsafe(14)
        print('ADMIN_PASSWORD сгенерирован автоматически.')
    else:
        base['ADMIN_PASSWORD'] = ask('ADMIN_PASSWORD для входа в админку', base['ADMIN_PASSWORD'])
    base['ALLOW_TOKEN_QUERY'] = ask('Разрешить token в URL для CSV-экспорта? true/false', base.get('ALLOW_TOKEN_QUERY', 'true')).lower()

    base['PAYMENT_PROVIDER'] = ask('Платежный провайдер: fake/yookassa', base.get('PAYMENT_PROVIDER', 'fake')).lower()
    if base['PAYMENT_PROVIDER'] == 'yookassa':
        base['YOOKASSA_SHOP_ID'] = ask('YOOKASSA_SHOP_ID', base.get('YOOKASSA_SHOP_ID', ''))
        base['YOOKASSA_SECRET_KEY'] = ask('YOOKASSA_SECRET_KEY', base.get('YOOKASSA_SECRET_KEY', ''))

    ENV_PATH.write_text(render_env(base), encoding='utf-8')
    print('\n.env готов.')
    print('Дальше: ./run.sh')
    print('Админка: http://localhost:8000/admin-ui')
    print(f'Логин админки: {base.get("ADMIN_USERNAME", "admin")}')
    print(f'Пароль админки: {base.get("ADMIN_PASSWORD", "см. .env")}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
