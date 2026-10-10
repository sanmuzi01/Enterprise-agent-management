"""员工自助绑定飞书 / 钉钉账号的一次性绑定码。

Revision ID: 20261011_0005
Revises: 20261011_0004
"""
from alembic import op
import sqlalchemy as sa

revision = "20261011_0005"
down_revision = "20261011_0004"
branch_labels = None
depends_on = None


def upgrade():
    if "external_bind_code" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "external_bind_code",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("used_by_external_id", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("idx_external_bind_code_hash", "external_bind_code", ["provider", "code_hash"])
    op.create_index("idx_external_bind_code_user", "external_bind_code", ["user_id", "provider"])


def downgrade():
    op.drop_table("external_bind_code")
