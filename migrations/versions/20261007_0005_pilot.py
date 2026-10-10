"""试点数据：手工办理计时样本、AI 成果评价。

Revision ID: 20261007_0005
Revises: 20261007_0004
"""
from alembic import op
import sqlalchemy as sa

revision = "20261007_0005"
down_revision = "20261007_0004"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("pilot_time_sample"):
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
    if not inspector.has_table("work_feedback"):
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


def downgrade():
    op.drop_table("work_feedback")
    op.drop_table("pilot_time_sample")
