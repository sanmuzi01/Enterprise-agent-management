"""扩大协作平台 Verification Token 列，为加密存储预留空间。

Revision ID: 20261011_0004
Revises: 20261011_0003
"""
from alembic import op
import sqlalchemy as sa

revision = "20261011_0004"
down_revision = "20261011_0003"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"]: column for column in inspector.get_columns("collaboration_app")}
    column = columns.get("verification_token")
    if column is not None and not isinstance(column["type"], sa.Text):
        op.alter_column(
            "collaboration_app",
            "verification_token",
            existing_type=column["type"],
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade():
    op.alter_column(
        "collaboration_app",
        "verification_token",
        existing_type=sa.Text(),
        type_=sa.String(length=200),
        existing_nullable=True,
    )
