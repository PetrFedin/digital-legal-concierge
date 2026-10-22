"""Separate client POA report from staff-confirmed receipt.

Existing cases are deliberately not backfilled: a verified receipt is not
evidence that the client previously pressed the Telegram confirmation button.
"""
from alembic import op
import sqlalchemy as sa


revision = "20261022_0021"
down_revision = "20261013_0020"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("cases")}
    if "poa_client_confirmed_at" not in columns:
        op.add_column(
            "cases",
            sa.Column("poa_client_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade():
    raise RuntimeError("POA confirmation evidence must be retained; use a reviewed forward migration.")
