import asyncio
import sys
from pathlib import Path

from sqlalchemy import inspect, select, text

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models import Base
from app.models.admin_user import AdminUser
from app.models.lawyer import Lawyer
from app.security.access_control import hash_password
from app.system.settings_service import SettingsService


async def get_or_create_lawyer(db):
    result = await db.execute(select(Lawyer).where(Lawyer.email == "lawyer@example.com"))
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
        await db.execute(text("ALTER TABLE admin_users ADD COLUMN username VARCHAR(100)"))
    if "telegram_id" not in columns:
        await db.execute(text("ALTER TABLE admin_users ADD COLUMN telegram_id BIGINT"))
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
            await db.execute(text(f"ALTER TABLE consultations ADD COLUMN {name} {ddl}"))
    await db.execute(
        text(
            "UPDATE consultations SET subject_type='new_or_other' "
            "WHERE subject_type IS NULL OR subject_type=''"
        )
    )
    await db.commit()


PAYMENT_MIGRATION_COLUMN_DDL = {
    "sqlite": {
        "processing_outcome": (
            "ALTER TABLE payments ADD COLUMN processing_outcome VARCHAR(50)"
        ),
        "processed_at": "ALTER TABLE payments ADD COLUMN processed_at DATETIME",
        "manual_review_required": (
            "ALTER TABLE payments ADD COLUMN manual_review_required "
            "BOOLEAN NOT NULL DEFAULT 0"
        ),
        "processing_error": "ALTER TABLE payments ADD COLUMN processing_error TEXT",
    },
    "postgresql": {
        "processing_outcome": (
            "ALTER TABLE payments ADD COLUMN processing_outcome VARCHAR(50)"
        ),
        "processed_at": "ALTER TABLE payments ADD COLUMN processed_at TIMESTAMPTZ",
        "manual_review_required": (
            "ALTER TABLE payments ADD COLUMN manual_review_required "
            "BOOLEAN NOT NULL DEFAULT FALSE"
        ),
        "processing_error": "ALTER TABLE payments ADD COLUMN processing_error TEXT",
    },
}

PAYMENT_PROVIDER_UNIQUE_INDEX_DDL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_provider_payment_id "
    "ON payments(provider, provider_payment_id) "
    "WHERE provider IS NOT NULL AND provider_payment_id IS NOT NULL"
)


class DuplicateProviderPaymentError(RuntimeError):
    """Raised when legacy provider payment duplicates block the unique index."""


async def portable_table_columns(db, table_name: str) -> set[str]:
    connection = await db.connection()

    def load_columns(sync_connection):
        inspector = inspect(sync_connection)
        if not inspector.has_table(table_name):
            return set()
        return {column["name"] for column in inspector.get_columns(table_name)}

    return await connection.run_sync(load_columns)


async def migrate_payments(db):
    dialect_name = db.bind.dialect.name
    additions = PAYMENT_MIGRATION_COLUMN_DDL.get(dialect_name)
    if additions is None:
        return
    columns = await portable_table_columns(db, "payments")
    if not columns:
        return
    for name, statement in additions.items():
        if name not in columns:
            await db.execute(text(statement))
    duplicate = (
        await db.execute(
            text(
                "SELECT provider, provider_payment_id, COUNT(*) AS duplicate_count "
                "FROM payments "
                "WHERE provider IS NOT NULL AND provider_payment_id IS NOT NULL "
                "GROUP BY provider, provider_payment_id "
                "HAVING COUNT(*) > 1 LIMIT 1"
            )
        )
    ).first()
    if duplicate:
        raise DuplicateProviderPaymentError(
            "Cannot create payment provider uniqueness index: "
            f"duplicate ({duplicate.provider!r}, {duplicate.provider_payment_id!r})"
        )
    await db.execute(
        text(PAYMENT_PROVIDER_UNIQUE_INDEX_DDL)
    )
    await db.commit()


async def get_or_create_admin(db):
    result = await db.execute(select(AdminUser).where(AdminUser.email == "admin@example.com"))
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
        await migrate_consultations(db)
        await migrate_payments(db)
        await get_or_create_lawyer(db)
        await get_or_create_admin(db)
        await SettingsService(db).bootstrap_defaults()
        await db.commit()
    print("База данных инициализирована")


if __name__ == "__main__":
    asyncio.run(main())
