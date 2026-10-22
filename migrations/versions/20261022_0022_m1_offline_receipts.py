"""Preserve evidence for actual offline M1 payments confirmed by an administrator."""
from alembic import op
import sqlalchemy as sa


revision = "20261022_0022"
down_revision = "20261022_0021"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("payments")}
    has_confirmer_fk = any(
        fk.get("constrained_columns") == ["confirmed_by"]
        and fk.get("referred_table") == "admin_users"
        for fk in inspector.get_foreign_keys("payments")
    )
    # Bootstrap can already contain these columns; existing invoices stay nullable.
    with op.batch_alter_table("payments") as batch:
        for column in (
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("confirmed_by", sa.Integer(), nullable=True),
            sa.Column("confirmation_reason", sa.Text(), nullable=True),
            sa.Column("receipt_reference", sa.String(255), nullable=True),
        ):
            if column.name not in columns:
                batch.add_column(column)
        if not has_confirmer_fk:
            batch.create_foreign_key(
                "fk_payments_confirmed_by_admin_users", "admin_users",
                ["confirmed_by"], ["id"],
            )


def downgrade():
    raise RuntimeError(
        "Payment receipt evidence must be retained; use a reviewed forward migration."
    )
