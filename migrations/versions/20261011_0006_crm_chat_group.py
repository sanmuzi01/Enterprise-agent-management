"""按群开启的“群消息记录到 CRM”：开启的群（crm_chat_group）和还没整理成客户活动的暂存消息（crm_chat_message）。

Revision ID: 20261011_0006
Revises: 20261011_0005
"""
from alembic import op
import sqlalchemy as sa

revision = "20261011_0006"
down_revision = "20261011_0005"
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "crm_chat_group" not in tables:
        op.create_table(
            "crm_chat_group",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("provider", sa.String(20), nullable=False),
            sa.Column("tenant_id", sa.String(120), nullable=False),
            sa.Column("chat_id", sa.String(120), nullable=False),
            sa.Column("chat_name", sa.String(200), nullable=True),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("customer_id", sa.Integer(), nullable=True),
            sa.Column("customer_name", sa.String(200), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("enabled_by", sa.Integer(), nullable=False),
            sa.Column("enabled_at", sa.DateTime(), nullable=False),
            sa.Column("closed_by", sa.Integer(), nullable=True),
            sa.Column("closed_at", sa.DateTime(), nullable=True),
            sa.Column("close_reason", sa.String(200), nullable=True),
            sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_message_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("provider", "tenant_id", "chat_id", name="uq_crm_chat_group"),
        )
        op.create_index("idx_crm_chat_group_team", "crm_chat_group", ["team_id", "status"])
    if "crm_chat_message" not in tables:
        op.create_table(
            "crm_chat_message",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("group_id", sa.Integer(), nullable=False),
            sa.Column("message_id", sa.String(120), nullable=False),
            sa.Column("sender_id", sa.String(120), nullable=True),
            sa.Column("sender_name", sa.String(120), nullable=True),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("sent_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("group_id", "message_id", name="uq_crm_chat_message"),
        )
        op.create_index("idx_crm_chat_message_sent", "crm_chat_message", ["group_id", "sent_at"])


def downgrade():
    op.drop_table("crm_chat_message")
    op.drop_table("crm_chat_group")
