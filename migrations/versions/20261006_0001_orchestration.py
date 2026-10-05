"""跨部门协同办理：协同计划与步骤。

Revision ID: 20261006_0001
Revises: 20261003_0002
"""
from alembic import op
import sqlalchemy as sa

revision = "20261006_0001"
down_revision = "20261003_0002"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("orchestration_plan"):
        op.create_table(
            "orchestration_plan",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id", name="fk_orch_plan_user"), nullable=False),
            sa.Column("team_id", sa.Integer, sa.ForeignKey("teams.id", name="fk_orch_plan_team"), nullable=False),
            sa.Column("source_text", sa.Text, nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False),
        )
        op.create_index("idx_orch_plan_user", "orchestration_plan", ["user_id", "created_at"])
    if not inspector.has_table("orchestration_step"):
        op.create_table(
            "orchestration_step",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("plan_id", sa.Integer, sa.ForeignKey("orchestration_plan.id", name="fk_orch_step_plan"), nullable=False),
            sa.Column("seq", sa.Integer, nullable=False),
            sa.Column("kind", sa.String(30), nullable=True),
            sa.Column("department_code", sa.String(20), nullable=True),
            sa.Column("clause", sa.Text, nullable=False),
            sa.Column("reason", sa.String(300), nullable=False),
            sa.Column("state", sa.String(20), nullable=False),
            sa.Column("automation_work_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=False),
        )
        op.create_index("idx_orch_step_plan", "orchestration_step", ["plan_id", "seq"])


def downgrade():
    op.drop_table("orchestration_step")
    op.drop_table("orchestration_plan")
