from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / '.env'
REPORT_DIR = ROOT / 'reports'
REPORT_DIR.mkdir(exist_ok=True)


def read_env() -> dict[str, str]:
    data: dict[str, str] = {}
    if ENV.exists():
        for line in ENV.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            data[k.strip()] = v.strip()
    return data


def mask(value: str) -> str:
    if not value:
        return ''
    if len(value) <= 8:
        return '***'
    return value[:4] + '***' + value[-4:]


def main() -> int:
    env = read_env()
    checks = []

    def add(name: str, ok: bool, advice: str = ''):
        checks.append({'name': name, 'ok': bool(ok), 'advice': advice})

    add('Файл .env существует', ENV.exists(), 'Запустите ./run.sh или cp .env.example .env')
    add('BOT_TOKEN заполнен', bool(env.get('BOT_TOKEN') and env.get('BOT_TOKEN') != 'CHANGE_ME'), 'Получите токен у BotFather и внесите BOT_TOKEN')
    add('RUN_BOT включен для реальной работы', env.get('RUN_BOT', '').lower() == 'true', 'Для теста можно false, для запуска в Telegram нужно true')
    add('ADMIN_API_TOKEN изменен', bool(env.get('ADMIN_API_TOKEN') and env.get('ADMIN_API_TOKEN') != 'dev-admin-token'), 'Задайте длинный случайный ADMIN_API_TOKEN')
    add('ADMIN_PASSWORD задан', bool(env.get('ADMIN_PASSWORD')), 'Задайте пароль для входа в /login')
    add('PAYMENT_WEBHOOK_SECRET изменен', bool(env.get('PAYMENT_WEBHOOK_SECRET') and env.get('PAYMENT_WEBHOOK_SECRET') != 'change-this-payment-secret'), 'Задайте отдельный секрет webhook')
    add('PUBLIC_BASE_URL задан', bool(env.get('PUBLIC_BASE_URL')), 'Для production укажите публичный https URL')

    provider = env.get('PAYMENT_PROVIDER', 'fake')
    if provider == 'yookassa':
        add('YooKassa shop id задан', bool(env.get('YOOKASSA_SHOP_ID')), 'Заполните YOOKASSA_SHOP_ID')
        add('YooKassa secret задан', bool(env.get('YOOKASSA_SECRET_KEY')), 'Заполните YOOKASSA_SECRET_KEY')
    else:
        add('Платежи в тестовом режиме', provider == 'fake', 'Для боевых платежей установите PAYMENT_PROVIDER=yookassa')

    for d in ['storage', 'logs', 'backups']:
        path = ROOT / d
        path.mkdir(exist_ok=True)
        add(f'Папка {d} доступна', path.exists() and os.access(path, os.W_OK), f'Проверьте права на папку {d}')

    docs = [
        ROOT / 'docs' / 'source_specs' / 'UX_UI_спецификация_Telegram_бот.docx',
        ROOT / 'docs' / 'source_specs' / 'Функциональная_спецификация_MVP.docx',
        ROOT / 'docs' / 'source_specs' / 'Карта_действий_v4.pdf',
    ]
    add('Исходные спецификации приложены', all(x.exists() for x in docs), 'Проверьте docs/source_specs')

    ok = all(x['ok'] for x in checks)
    report = {
        'version': '1.0.0-v19',
        'ok': ok,
        'env_preview': {k: mask(v) for k, v in env.items() if k in {'BOT_TOKEN','ADMIN_API_TOKEN','ADMIN_PASSWORD','PAYMENT_WEBHOOK_SECRET','PUBLIC_BASE_URL','PAYMENT_PROVIDER'}},
        'checks': checks,
        'next': [
            './run.sh',
            'open http://localhost:8000/launch-assistant',
            'open http://localhost:8000/admin-ui',
            'Написать /start боту в Telegram',
        ],
    }
    (REPORT_DIR / 'preflight_v19.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Preflight v19', '', f"Статус: {'OK' if ok else 'НУЖНЫ ПРАВКИ'}", '']
    for item in checks:
        lines.append(f"- {'✅' if item['ok'] else '❌'} {item['name']}")
        if not item['ok'] and item['advice']:
            lines.append(f"  - Что сделать: {item['advice']}")
    (REPORT_DIR / 'preflight_v19.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1

if __name__ == '__main__':
    raise SystemExit(main())
