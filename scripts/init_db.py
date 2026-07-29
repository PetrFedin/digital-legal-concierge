import asyncio
import sys
from pathlib import Path

from sqlalchemy import select, text

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models import Base
from app.models.admin_user import AdminUser
from app.models.lawyer import Lawyer
from app.security.access_control import hash_password
from app.system.settings_service import SettingsService


async def get_or_create_lawyer(db):
    result = await db.execute(
        select(Lawyer).where(Lawyer.email == "lawyer@example.com")
    )
    lawyer = result.scalars().first()
    if not lawyer:
        db.add(
            Lawyer(
                full_name="Дежурный юрист",
                email="lawyer@example.com",
                specialization="ДДУ 214-ФЗ",
            )
        )


async def table_columns(db, table_name: str) -> set[str]:
    rows = (await db.execute(text(f"PRAGMA table_info({table_name})"))).all()
    return {row[1] for row in rows}


async def migrate_admin_users(db):
    if not settings.database_url.startswith("sqlite"):
        return
    columns = await table_columns(db, "admin_users")
    if not columns:
        return
    if "username" not in columns:
        await db.execute(
            text("ALTER TABLE admin_users ADD COLUMN username VARCHAR(100)")
        )
    if "telegram_id" not in columns:
        await db.execute(
            text("ALTER TABLE admin_users ADD COLUMN telegram_id BIGINT")
        )
    await db.commit()


async def migrate_cases(db):
    if not settings.database_url.startswith("sqlite"):
        return
    columns = await table_columns(db, "cases")
    if not columns:
        return
    additions = {
        "assigned_at": "DATETIME",
        "first_lawyer_response_at": "DATETIME",
        "last_lawyer_activity_at": "DATETIME",
        "sla_due_at": "DATETIME",
        "sla_status": "VARCHAR(50) DEFAULT 'NOT_STARTED'",
        "escalation_level": "INTEGER DEFAULT 0",
    }
    for name, ddl in additions.items():
        if name not in columns:
            await db.execute(text(f"ALTER TABLE cases ADD COLUMN {name} {ddl}"))
    await db.execute(
        text(
            "UPDATE cases SET sla_status='NOT_STARTED' "
            "WHERE sla_status IS NULL OR sla_status=''"
        )
    )
    await db.execute(
        text(
            "UPDATE cases SET escalation_level=0 "
            "WHERE escalation_level IS NULL"
        )
    )
    for index_name, column_name in {
        "ix_cases_assigned_at": "assigned_at",
        "ix_cases_last_lawyer_activity_at": "last_lawyer_activity_at",
        "ix_cases_sla_due_at": "sla_due_at",
        "ix_cases_sla_status": "sla_status",
    }.items():
        await db.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {index_name} "
                f"ON cases ({column_name})"
            )
        )
    await db.commit()


async def migrate_lawyers(db):
    if not settings.database_url.startswith("sqlite"):
        return
    columns = await table_columns(db, "lawyers")
    if not columns:
        return
    if "telegram_id" not in columns:
        await db.execute(text("ALTER TABLE lawyers ADD COLUMN telegram_id BIGINT"))
    await db.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_lawyers_telegram_id "
            "ON lawyers (telegram_id) WHERE telegram_id IS NOT NULL"
        )
    )
    await db.commit()


async def migrate_consultations(db):
    if not settings.database_url.startswith("sqlite"):
        return
    columns = await table_columns(db, "consultations")
    if not columns:
        return
    additions = {
        "related_case_id": "INTEGER",
        "slot_id": "INTEGER",
        "subject_type": "VARCHAR(50) DEFAULT 'new_or_other'",
    }
    for name, ddl in additions.items():
        if name not in columns:
            await db.execute(
                text(f"ALTER TABLE consultations ADD COLUMN {name} {ddl}")
            )
    await db.execute(
        text(
            "UPDATE consultations SET subject_type='new_or_other' "
            "WHERE subject_type IS NULL OR subject_type=''"
        )
    )
    await db.commit()


async def migrate_payments(db):
    if not settings.database_url.startswith("sqlite"):
        return
    columns = await table_columns(db, "payments")
    if not columns:
        return
    if "reservation_key" not in columns:
        await db.execute(
            text("ALTER TABLE payments ADD COLUMN reservation_key VARCHAR(255)")
        )
    await db.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_payments_reservation_key "
            "ON payments (reservation_key)"
        )
    )
    await db.commit()


async def migrate_notifications(db):
    if not settings.database_url.startswith("sqlite"):
        return
    columns = await table_columns(db, "notifications")
    if not columns:
        return
    additions = {
        "recipient_type": "VARCHAR(50)",
        "target_chat_id": "BIGINT",
        "dedupe_key": "VARCHAR(255)",
        "attempt_count": "INTEGER DEFAULT 0",
        "last_error": "TEXT",
        "next_attempt_at": "DATETIME",
        "sent_at": "DATETIME",
    }
    for name, ddl in additions.items():
        if name not in columns:
            await db.execute(
                text(f"ALTER TABLE notifications ADD COLUMN {name} {ddl}")
            )
    await db.execute(
        text(
            "UPDATE notifications SET attempt_count=0 "
            "WHERE attempt_count IS NULL"
        )
    )
    await db.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_notifications_recipient_type "
            "ON notifications (recipient_type)"
        )
    )
    await db.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_notifications_target_chat_id "
            "ON notifications (target_chat_id)"
        )
    )
    await db.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_notifications_next_attempt_at "
            "ON notifications (next_attempt_at)"
        )
    )
    await db.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_notifications_dedupe_key "
            "ON notifications (dedupe_key) WHERE dedupe_key IS NOT NULL"
        )
    )
    await db.commit()


async def get_or_create_admin(db):
    result = await db.execute(
        select(AdminUser).where(AdminUser.email == "admin@example.com")
    )
    admin = result.scalars().first()
    initial_password = settings.admin_password or settings.admin_api_token
    if not admin:
        db.add(
            AdminUser(
                full_name="Владелец системы",
                username=settings.admin_username or "admin",
                email="admin@example.com",
                password_hash=hash_password(initial_password),
                role="superadmin,admin",
                is_active=True,
            )
        )
    else:
        admin.username = admin.username or settings.admin_username or "admin"
        admin.role = "superadmin,admin"
        admin.is_active = True
        if not admin.password_hash or admin.password_hash == "dev":
            admin.password_hash = hash_password(initial_password)


async def main():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        await migrate_admin_users(db)
        await migrate_cases(db)
        await migrate_lawyers(db)
        await migrate_consultations(db)
        await migrate_payments(db)
        await migrate_notifications(db)
        await get_or_create_lawyer(db)
        await get_or_create_admin(db)
        await SettingsService(db).bootstrap_defaults()
        await db.commit()
    print("База данных инициализирована")


if __name__ == "__main__":
    asyncio.run(main())
