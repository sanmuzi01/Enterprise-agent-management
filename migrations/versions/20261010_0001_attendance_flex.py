"""考勤规则加“弹性上班”：上班时间之后多少分钟内到岗都不算迟到，晚到多少晚走多少（0 = 不弹性，和以前一样）。

Revision ID: 20261010_0001
Revises: 20261009_0002
"""
from alembic import op
import sqlalchemy as sa

revision = "20261010_0001"
down_revision = "20261009_0002"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("attendance_rule")}
    if "flex_minutes" not in columns:
        op.add_column("attendance_rule", sa.Column("flex_minutes", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("attendance_rule", "flex_minutes")
