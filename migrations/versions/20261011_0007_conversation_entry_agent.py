"""会话记下“从哪个助手发起”（conversation.entry_agent_id）：经中央助手转交的会话归部门助手，
中央助手的会话列表靠这个字段把它们列出来（不然员工回到中央助手页面，聊过的内容就“不见了”）。

已有数据按转交记录回填：同一个人、转交的目标助手、转交后 2 分钟内新建的会话，记为从中央助手发起。

Revision ID: 20261011_0007
Revises: 20261011_0006
"""
from alembic import op
import sqlalchemy as sa

revision = "20261011_0007"
down_revision = "20261011_0006"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("conversation")}
    if "entry_agent_id" not in columns:
        op.add_column("conversation", sa.Column("entry_agent_id", sa.Integer(), nullable=True))
        op.create_index("idx_conversation_entry_agent", "conversation", ["user_id", "entry_agent_id"])
    op.execute(
        "UPDATE conversation c JOIN agent_handoff h "
        "ON c.user_id = h.user_id AND c.agent_id = h.target_agent_id "
        "AND c.create_time BETWEEN h.created_at - INTERVAL 5 SECOND AND h.created_at + INTERVAL 120 SECOND "
        "SET c.entry_agent_id = h.central_agent_id "
        "WHERE c.entry_agent_id IS NULL AND h.central_agent_id IS NOT NULL AND h.target_agent_id <> h.central_agent_id"
    )


def downgrade():
    op.drop_index("idx_conversation_entry_agent", table_name="conversation")
    op.drop_column("conversation", "entry_agent_id")
