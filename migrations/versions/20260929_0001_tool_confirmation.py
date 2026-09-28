"""高风险 Agent 工具调用的确认单表（第五轮审计 P0-2，
docs/enterprise-rbac-plan.md）。

新增 `tool_confirmation` 表：ReAct 循环里 LLM 请求调用 high_risk 工具时，
`service/tools/langchain_adapter.py` 建一条 pending 记录，真正执行只能经
`service/tool_confirmation_service.py` 在用户点击确认后单独触发，跟 ReAct
循环彼此隔离。

Revision ID: 20260929_0001
Revises: 20260928_0002
Create Date: 2026-09-29
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20260929_0001'
down_revision: Union[str, Sequence[str], None] = '20260928_0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tool_confirmation',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('token', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=True),
        sa.Column('tool_name', sa.String(length=80), nullable=False),
        sa.Column('tool_args', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('result', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], name='fk_toolconf_user'),
    )
    op.create_unique_constraint('uq_tool_confirmation_token', 'tool_confirmation', ['token'])
    op.create_index(
        'idx_tool_confirmation_user_status', 'tool_confirmation', ['user_id', 'status'],
    )


def downgrade() -> None:
    # 直接 drop_table：MySQL 要求外键约束的引用列必须有索引兜底，
    # idx_tool_confirmation_user_status（user_id 打头）正是 fk_toolconf_user
    # 依赖的那个索引，单独先 drop_index 会报 1553（索引被外键约束占用），
    # drop_table 整体删表不受这个顺序限制。
    op.drop_table('tool_confirmation')
