import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))
import asyncio
from sqlalchemy import select, text
from app.db.session import engine, AsyncSessionLocal
from app.models import Base
from app.models.lawyer import Lawyer
from app.models.admin_user import AdminUser
from app.system.settings_service import SettingsService
from app.config import settings
from app.security.access_control import hash_password

async def get_or_create_lawyer(db):
    result = await db.execute(select(Lawyer).where(Lawyer.email == 'lawyer@example.com'))
    lawyer = result.scalars().first()
    if not lawyer:
        db.add(Lawyer(full_name='Дежурный юрист', email='lawyer@example.com', specialization='ДДУ 214-ФЗ'))

async def migrate_admin_users(db):
    if not settings.database_url.startswith('sqlite'):
        return
    rows = (await db.execute(text("PRAGMA table_info(admin_users)"))).all()
    columns = {row[1] for row in rows}
    if not columns:
        return
    if 'username' not in columns:
        await db.execute(text("ALTER TABLE admin_users ADD COLUMN username VARCHAR(100)"))
    if 'telegram_id' not in columns:
        await db.execute(text("ALTER TABLE admin_users ADD COLUMN telegram_id BIGINT"))
    await db.commit()


async def get_or_create_admin(db):
    result = await db.execute(select(AdminUser).where(AdminUser.email == 'admin@example.com'))
    admin = result.scalars().first()
    initial_password = settings.admin_password or settings.admin_api_token
    if not admin:
        db.add(AdminUser(full_name='Владелец системы', username=settings.admin_username or 'admin', email='admin@example.com', password_hash=hash_password(initial_password), role='superadmin,admin', is_active=True))
    else:
        admin.username = admin.username or settings.admin_username or 'admin'
        admin.role = 'superadmin,admin'
        admin.is_active = True
        if not admin.password_hash or admin.password_hash == 'dev':
            admin.password_hash = hash_password(initial_password)

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        await migrate_admin_users(db)
        await get_or_create_lawyer(db)
        await get_or_create_admin(db)
        await SettingsService(db).bootstrap_defaults()
        await db.commit()
    print('База данных инициализирована')

if __name__ == '__main__':
    asyncio.run(main())
