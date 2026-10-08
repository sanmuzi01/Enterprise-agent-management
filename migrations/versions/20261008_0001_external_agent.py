"""外部 Agent 接入：agent.runtime_type + agent_external_endpoint。

Revision ID: 20261008_0001
Revises: 20261007_0006
"""
from alembic import op
import sqlalchemy as sa

revision = "20261008_0001"
down_revision = "20261007_0006"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("agent")}
    if "runtime_type" not in columns:
        op.add_column("agent", sa.Column("runtime_type", sa.String(20), nullable=False, server_default="builtin"))
    if not inspector.has_table("agent_external_endpoint"):
        op.create_table(
            "agent_external_endpoint",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("agent_id", sa.Integer, sa.ForeignKey("agent.id", name="fk_agent_external_endpoint_agent"), nullable=False),
            sa.Column("url", sa.String(1000), nullable=False),
            sa.Column("secret_encrypted", sa.Text, nullable=False),
            sa.Column("headers_encrypted", sa.Text),
            sa.Column("timeout_seconds", sa.Integer, nullable=False),
            sa.Column("send_knowledge", sa.Integer, nullable=False),
            sa.Column("last_test_at", sa.DateTime),
            sa.Column("last_test_ok", sa.Integer),
            sa.Column("last_test_message", sa.String(300)),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False),
            sa.UniqueConstraint("agent_id", name="uq_agent_external_endpoint_agent"),
        )


def downgrade():
    op.drop_table("agent_external_endpoint")
    op.drop_column("agent", "runtime_type")
