from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from sqlalchemy import select, func

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.models.lawyer import Lawyer
from app.models.admin_user import AdminUser
from app.models.system_setting import SystemSetting


async def main() -> int:
    checks: list[tuple[bool, str, str]] = []
    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    checks.append((Path(settings.storage_dir).exists(), 'Хранилище документов создано', f'mkdir -p {settings.storage_dir}'))
    checks.append((bool(settings.admin_api_token), 'ADMIN_API_TOKEN задан', 'запустите python scripts/first_run.py'))
    checks.append((not settings.run_bot or settings.bot_token not in ('', 'CHANGE_ME', None), 'BOT_TOKEN задан при RUN_BOT=true', 'укажите BOT_TOKEN в .env'))
    
    if settings.app_env == 'production':
        checks.append((settings.payment_webhook_secret not in ('', 'dev-payment-secret', 'change-this-payment-secret', None), 'PAYMENT_WEBHOOK_SECRET не dev', 'запустите python scripts/first_run.py'))
    else:
        checks.append((bool(settings.payment_webhook_secret), 'PAYMENT_WEBHOOK_SECRET задан для локального режима', 'запустите python scripts/first_run.py'))

    async with AsyncSessionLocal() as db:
        lawyer_count = (await db.execute(select(func.count(Lawyer.id)))).scalar_one()
        admin_count = (await db.execute(select(func.count(AdminUser.id)))).scalar_one()
        settings_count = (await db.execute(select(func.count(SystemSetting.id)))).scalar_one()
        checks.append((lawyer_count > 0, 'Есть хотя бы один юрист', 'python scripts/init_db.py'))
        checks.append((admin_count > 0, 'Есть администратор', 'python scripts/init_db.py'))
        checks.append((settings_count > 0, 'Настройки загружены', 'python scripts/init_db.py'))

    ok_all = True
    print('\nОператорская проверка готовности\n')
    for ok, title, fix in checks:
        print(f"[{'OK' if ok else 'FAIL'}] {title}" + ('' if ok else f' → {fix}'))
        ok_all = ok_all and ok

    print('\nМинимальный запуск: ./run.sh')
    print('Docker запуск: ./docker-start.sh')
    print('Проверка после запуска: ./status.sh')
    return 0 if ok_all else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
