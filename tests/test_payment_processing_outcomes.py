import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateColumn, CreateIndex

from app.domain.payments.payment_processing_outcomes import (
    MANUAL_REVIEW_OUTCOMES,
    PaymentProcessingOutcome,
)
from app.models import Base
from app.models.payment import Payment

sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.init_db import (
    DuplicateProviderPaymentError,
    PAYMENT_MIGRATION_COLUMN_DDL,
    PAYMENT_PROVIDER_UNIQUE_INDEX_DDL,
    migrate_payments,
)


@pytest.fixture
async def payment_db(tmp_path):
    database_path = tmp_path / "payments.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        yield session_factory
    finally:
        await engine.dispose()


def make_payment(**overrides):
    values = {
        "case_id": 1,
        "payment_code": "M2_CONSULTATION_PAYMENT",
        "title": "Оплата консультации",
        "amount": Decimal("1000.00"),
        "currency": "RUB",
        "status": "WAITING_CONFIRMATION",
    }
    values.update(overrides)
    return Payment(**values)


@pytest.mark.parametrize("outcome", list(PaymentProcessingOutcome))
async def test_saves_each_processing_outcome(payment_db, outcome):
    async with payment_db() as session:
        payment = make_payment(
            processing_outcome=outcome,
            manual_review_required=outcome in MANUAL_REVIEW_OUTCOMES,
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)

        assert isinstance(payment.processing_outcome, PaymentProcessingOutcome)
        assert payment.processing_outcome == outcome
        assert payment.manual_review_required is (outcome in MANUAL_REVIEW_OUTCOMES)


async def test_processed_outcome_does_not_require_manual_review(payment_db):
    async with payment_db() as session:
        payment = make_payment(
            processing_outcome=PaymentProcessingOutcome.PROCESSED,
            manual_review_required=False,
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)

        assert payment.processing_outcome is PaymentProcessingOutcome.PROCESSED
        assert payment.manual_review_required is False


async def test_manual_review_outcome_allows_manual_review_flag(payment_db):
    async with payment_db() as session:
        payment = make_payment(
            processing_outcome=PaymentProcessingOutcome.MANUAL_REVIEW_REQUIRED,
            manual_review_required=True,
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)

        assert payment.processing_outcome == PaymentProcessingOutcome.MANUAL_REVIEW_REQUIRED
        assert payment.manual_review_required is True


async def test_conflict_outcome_is_persisted_for_manual_handling(payment_db):
    async with payment_db() as session:
        payment = make_payment(
            processing_outcome=PaymentProcessingOutcome.CONFLICT,
            manual_review_required=True,
            processing_error="consultation and slot state conflict",
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)

        assert payment.processing_outcome == PaymentProcessingOutcome.CONFLICT
        assert payment.manual_review_required is True
        assert payment.processing_error == "consultation and slot state conflict"


def test_already_processed_is_not_a_persisted_processing_outcome():
    with pytest.raises(ValueError):
        PaymentProcessingOutcome("already_processed")


def test_processing_timestamp_semantics_are_documented():
    semantics = Payment.__table__.c.processed_at.info["semantics"].lower()

    assert "webhook processing" in semantics
    assert "not the payment timestamp" in semantics


def test_manual_review_contract_is_documented():
    contract = Payment.__table__.c.manual_review_required.info["contract"]

    assert "MANUAL_REVIEW_REQUIRED" in contract
    assert "CONFLICT" in contract
    assert "False for PROCESSED" in contract


async def test_existing_payment_defaults_are_backward_compatible(payment_db):
    async with payment_db() as session:
        payment = make_payment()
        session.add(payment)
        await session.commit()
        await session.refresh(payment)

        assert payment.processing_outcome is None
        assert payment.processed_at is None
        assert payment.manual_review_required is False
        assert payment.processing_error is None


async def test_saves_processed_at(payment_db):
    processed_at = datetime.now(timezone.utc)
    async with payment_db() as session:
        payment = make_payment(processed_at=processed_at)
        session.add(payment)
        await session.commit()
        await session.refresh(payment)

        assert payment.processed_at is not None
        assert payment.processed_at.replace(tzinfo=timezone.utc) == processed_at


async def test_saves_processing_error(payment_db):
    async with payment_db() as session:
        payment = make_payment(processing_error="slot hold expired")
        session.add(payment)
        await session.commit()
        await session.refresh(payment)

        assert payment.processing_error == "slot hold expired"


async def test_duplicate_provider_payment_id_for_same_provider_is_rejected(payment_db):
    async with payment_db() as session:
        session.add_all(
            [
                make_payment(provider="yookassa", provider_payment_id="provider-payment-1"),
                make_payment(provider="yookassa", provider_payment_id="provider-payment-1"),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_same_provider_payment_id_for_different_providers_is_allowed(payment_db):
    async with payment_db() as session:
        session.add_all(
            [
                make_payment(provider="yookassa", provider_payment_id="shared-payment-id"),
                make_payment(provider="fake", provider_payment_id="shared-payment-id"),
            ]
        )
        await session.commit()


async def test_multiple_null_provider_payment_ids_are_allowed(payment_db):
    async with payment_db() as session:
        session.add_all(
            [
                make_payment(provider=None, provider_payment_id=None),
                make_payment(provider=None, provider_payment_id=None),
                make_payment(provider="fake", provider_payment_id=None),
                make_payment(provider="fake", provider_payment_id=None),
            ]
        )
        await session.commit()


async def test_fresh_sqlite_schema_contains_outcome_columns_and_partial_index(payment_db):
    async with payment_db() as session:
        connection = await session.connection()

        def inspect_schema(sync_connection):
            inspector = inspect(sync_connection)
            return (
                {column["name"]: column for column in inspector.get_columns("payments")},
                {index["name"]: index for index in inspector.get_indexes("payments")},
            )

        columns, indexes = await connection.run_sync(inspect_schema)

        assert columns["processing_outcome"]["nullable"] is True
        assert columns["processed_at"]["nullable"] is True
        assert columns["manual_review_required"]["nullable"] is False
        assert columns["processing_error"]["nullable"] is True
        assert indexes["uq_payments_provider_payment_id"]["unique"] == 1


async def create_legacy_payments_database(database_path, *, duplicate=False):
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE TABLE payments ("
                "id INTEGER PRIMARY KEY, provider VARCHAR(100), "
                "provider_payment_id VARCHAR(255))"
            )
        )
        if duplicate:
            await connection.execute(
                text(
                    "INSERT INTO payments (id, provider, provider_payment_id) VALUES "
                    "(1, 'yookassa', 'duplicate-id'), "
                    "(2, 'yookassa', 'duplicate-id')"
                )
            )
    return engine


async def test_legacy_sqlite_migration_is_idempotent(tmp_path):
    database_path = tmp_path / "legacy-payments.db"
    engine = await create_legacy_payments_database(database_path)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        await migrate_payments(session)
        await migrate_payments(session)
        columns = {
            row[1] for row in (await session.execute(text("PRAGMA table_info(payments)"))).all()
        }
        indexes = {
            row[1] for row in (await session.execute(text("PRAGMA index_list(payments)"))).all()
        }

        assert {
            "processing_outcome",
            "processed_at",
            "manual_review_required",
            "processing_error",
        } <= columns
        assert "uq_payments_provider_payment_id" in indexes

    await engine.dispose()


async def test_existing_duplicate_blocks_unique_index_creation(tmp_path):
    database_path = tmp_path / "duplicate-payments.db"
    engine = await create_legacy_payments_database(database_path, duplicate=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        with pytest.raises(
            DuplicateProviderPaymentError,
            match="duplicate.*yookassa.*duplicate-id",
        ):
            await migrate_payments(session)
        await session.rollback()

    await engine.dispose()


def test_postgresql_migration_ddl_contains_required_columns_and_partial_index():
    statements = PAYMENT_MIGRATION_COLUMN_DDL["postgresql"]

    assert statements["processing_outcome"].endswith("processing_outcome VARCHAR(50)")
    assert statements["processed_at"].endswith("processed_at TIMESTAMPTZ")
    assert statements["manual_review_required"].endswith(
        "manual_review_required BOOLEAN NOT NULL DEFAULT FALSE"
    )
    assert statements["processing_error"].endswith("processing_error TEXT")
    assert "ON payments(provider, provider_payment_id)" in PAYMENT_PROVIDER_UNIQUE_INDEX_DDL
    assert (
        "WHERE provider IS NOT NULL AND provider_payment_id IS NOT NULL"
        in PAYMENT_PROVIDER_UNIQUE_INDEX_DDL
    )


def test_payment_schema_compiles_for_postgresql():
    dialect = postgresql.dialect()
    expected_column_sql = {
        "processing_outcome": "processing_outcome VARCHAR(50)",
        "processed_at": "processed_at TIMESTAMP WITH TIME ZONE",
        "manual_review_required": "manual_review_required BOOLEAN DEFAULT false NOT NULL",
        "processing_error": "processing_error TEXT",
    }

    for column_name, expected_sql in expected_column_sql.items():
        compiled = str(CreateColumn(Payment.__table__.c[column_name]).compile(dialect=dialect))
        assert compiled == expected_sql

    index = next(
        index
        for index in Payment.__table__.indexes
        if index.name == "uq_payments_provider_payment_id"
    )
    compiled_index = str(CreateIndex(index).compile(dialect=dialect))
    assert compiled_index == (
        "CREATE UNIQUE INDEX uq_payments_provider_payment_id "
        "ON payments (provider, provider_payment_id) "
        "WHERE provider IS NOT NULL AND provider_payment_id IS NOT NULL"
    )
