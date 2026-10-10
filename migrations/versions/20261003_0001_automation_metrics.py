"""工作成果效果指标：保存时间、保存尝试次数，以及管理员可调的"手工办理基准时间"。

Revision ID: 20261003_0001
Revises: 20261002_0003
"""
from alembic import op
import sqlalchemy as sa

revision = "20261003_0001"
down_revision = "20261002_0003"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("automation_work")}
    if "applied_at" not in columns:
        op.add_column("automation_work", sa.Column("applied_at", sa.DateTime, nullable=True))
    if "apply_attempts" not in columns:
        op.add_column("automation_work", sa.Column("apply_attempts", sa.Integer, nullable=False, server_default="0"))
    # 已保存的历史成果没有精确的保存时间，用最后更新时间近似，并至少算一次尝试。
    op.execute("UPDATE automation_work SET applied_at = updated_at WHERE status = 'applied' AND applied_at IS NULL")
    op.execute("UPDATE automation_work SET apply_attempts = 1 WHERE status = 'applied' AND apply_attempts = 0")
    if not inspector.has_table("workflow_baseline"):
        op.create_table(
            "workflow_baseline",
            sa.Column("kind", sa.String(30), primary_key=True),
            sa.Column("minutes", sa.Float, nullable=False),
            sa.Column("updated_by", sa.Integer, nullable=True),
            sa.Column("updated_at", sa.DateTime, nullable=False),
        )


def downgrade():
    op.drop_table("workflow_baseline")
    op.drop_column("automation_work", "apply_attempts")
    op.drop_column("automation_work", "applied_at")
