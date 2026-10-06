"""Agent 运行记录：trace_id / 错误码 / 问题编号，失败的运行能直接查到对应的问题。

Revision ID: 20261007_0004
Revises: 20261007_0003
"""
from alembic import op
import sqlalchemy as sa

revision = "20261007_0004"
down_revision = "20261007_0003"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agent_run")}
    if "trace_id" not in columns:
        op.add_column("agent_run", sa.Column("trace_id", sa.String(64), nullable=True))
        op.add_column("agent_run", sa.Column("error_code", sa.String(60), nullable=True))
        op.add_column("agent_run", sa.Column("issue_no", sa.String(40), nullable=True))
        op.create_index("idx_agent_run_agent_status", "agent_run", ["agent_id", "status", "started_at"])


def downgrade():
    op.drop_index("idx_agent_run_agent_status", table_name="agent_run")
    for name in ("issue_no", "error_code", "trace_id"):
        op.drop_column("agent_run", name)
