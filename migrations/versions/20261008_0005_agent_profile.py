"""企业智能体档案：说明（它能做什么）与维护人（出了问题找谁）。

Revision ID: 20261008_0005
Revises: 20261008_0004
"""
from alembic import op
import sqlalchemy as sa

revision = "20261008_0005"
down_revision = "20261008_0004"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agent")}
    if "description" not in columns:
        op.add_column("agent", sa.Column("description", sa.String(500), nullable=True))
    if "maintainer" not in columns:
        op.add_column("agent", sa.Column("maintainer", sa.String(100), nullable=True))


def downgrade():
    op.drop_column("agent", "maintainer")
    op.drop_column("agent", "description")
