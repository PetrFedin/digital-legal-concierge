"""Forward-only isolated client archive grant table. Not a delivery endpoint."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "20261013_0020"
down_revision = "20261012_0019"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "client_document_grants" in inspect(bind).get_table_names():
        return
    op.create_table(
        "client_document_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(32), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("document_version", sa.Integer(), nullable=False),
        sa.Column("document_sha256", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("approval_reference", sa.String(120), nullable=False),
        sa.Column("token_key_id", sa.String(32), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("token_digest"),
    )
    for name,cols in [
        ("ix_client_document_grants_public_id",["public_id"]),
        ("ix_client_document_grants_user_id",["user_id"]),
        ("ix_client_document_grants_case_id",["case_id"]),
        ("ix_client_document_grants_document_id",["document_id"]),
        ("ix_client_document_grants_token_digest",["token_digest"]),
        ("ix_client_document_grants_status",["status"]),
        ("ix_client_document_grants_expires_at",["expires_at"]),
        ("ix_client_document_grants_user_expiry",["user_id","expires_at"]),
        ("ix_client_document_grants_case_status",["case_id","status"]),
    ]:
        op.create_index(name,"client_document_grants",cols)
    op.create_index(
        "uq_client_document_grants_issued", "client_document_grants",
        ["user_id","document_id"], unique=True,
        postgresql_where=text("status = 'ISSUED'"),
        sqlite_where=text("status = 'ISSUED'"),
    )


def downgrade():
    raise RuntimeError("Downgrade requires security review and grant revocation.")
