"""移除“AI 工作成果”的试点度量层：手工办理计时样本、成果评价、基准时间。

成果整理、核对、保存草稿这条主流程不受影响；只是不再统计“省了多少工时”，也不再收集评分。

Revision ID: 20261008_0003
Revises: 20261008_0002
"""
from alembic import op
import sqlalchemy as sa

revision = "20261008_0003"
down_revision = "20261008_0002"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    for table in ("pilot_time_sample", "work_feedback", "workflow_baseline"):
        if inspector.has_table(table):
            op.drop_table(table)


def downgrade():
    op.create_table(
        "workflow_baseline",
        sa.Column("kind", sa.String(30), primary_key=True),
        sa.Column("minutes", sa.Float, nullable=False),
        sa.Column("updated_by", sa.Integer, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "pilot_time_sample",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("team_id", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("minutes", sa.Float, nullable=False),
        sa.Column("note", sa.String(200)),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("idx_pilot_sample_kind", "pilot_time_sample", ["kind", "created_at"])
    op.create_index("idx_pilot_sample_user", "pilot_time_sample", ["user_id", "created_at"])
    op.create_table(
        "work_feedback",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("work_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("team_id", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("rating", sa.Integer, nullable=False),
        sa.Column("comment", sa.String(300)),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("work_id", "user_id", name="uq_work_feedback"),
    )
