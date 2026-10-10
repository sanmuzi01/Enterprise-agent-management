"""automation_work 增加 business_checks_json：整理结果的业务系统核对结论（库存、预算、重复发票等）。

Revision ID: 20261002_0002
Revises: 20261002_0001
"""
from alembic import op
import sqlalchemy as sa

revision = "20261002_0002"
down_revision = "20261002_0001"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("automation_work")}
    if "business_checks_json" not in columns:
        op.add_column("automation_work", sa.Column("business_checks_json", sa.Text, nullable=True))


def downgrade():
    op.drop_column("automation_work", "business_checks_json")
