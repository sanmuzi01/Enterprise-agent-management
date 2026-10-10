"""中央 Agent 转交记录：每次路由决定转给谁、为什么、输入摘要、备选部门。

Revision ID: 20261003_0002
Revises: 20261003_0001
"""
from alembic import op
import sqlalchemy as sa

revision = "20261003_0002"
down_revision = "20261003_0001"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("agent_handoff"):
        return
    op.create_table(
        "agent_handoff",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id", name="fk_agent_handoff_user"), nullable=False),
        sa.Column("central_agent_id", sa.Integer, nullable=True),
        sa.Column("target_agent_id", sa.Integer, nullable=True),
        sa.Column("reason", sa.String(24), nullable=False),
        sa.Column("department_code", sa.String(20), nullable=True),
        sa.Column("detail_json", sa.Text, nullable=True),
        sa.Column("message_excerpt", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("idx_agent_handoff_created", "agent_handoff", ["created_at"])
    op.create_index("idx_agent_handoff_user", "agent_handoff", ["user_id", "created_at"])


def downgrade():
    op.drop_table("agent_handoff")
