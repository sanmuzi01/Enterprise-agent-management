"""一次性初始化的哨兵表：自动建企业等“只能做一次”的初始化，用主键在数据库层面保证只做一次。

Revision ID: 20261009_0001
Revises: 20261008_0005
"""
from alembic import op
import sqlalchemy as sa

revision = "20261009_0001"
down_revision = "20261008_0005"
branch_labels = None
depends_on = None


def upgrade():
    if "bootstrap_marker" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "bootstrap_marker",
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("done_at", sa.DateTime(), nullable=False),
        sa.Column("detail", sa.String(length=200), nullable=True),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade():
    op.drop_table("bootstrap_marker")
