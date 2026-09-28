"""审批单去重：真正的数据库唯一约束（P1 并发修复，docs/enterprise-rbac-plan.md）。

`approval_request` 新增 `active_dedupe_key`（可空）+ 唯一约束。之前
`service/approval_service.py::request_or_get_pending` 是"先查一遍没有活跃单
才插入"，两个并发请求可能都通过检查、都插入一条，产生重复审批单——应用层的
"先查后插"防不住真正的并发，这里用数据库唯一约束兜底。MySQL 的唯一索引允许
多个 NULL 共存，只在非 NULL 值之间强制唯一，天然适合"只对活跃的那一条做唯一
约束"这个需求（这一列由 service 层显式维护，不是数据库生成列——生成列在
MySQL 里没法引用 NOW() 判断过期）。

Revision ID: 20260928_0001
Revises: 20260927_0003
Create Date: 2026-09-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20260928_0001'
down_revision: Union[str, Sequence[str], None] = '20260927_0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('approval_request', sa.Column('active_dedupe_key', sa.String(length=150), nullable=True))
    op.create_unique_constraint('uq_approval_active_dedupe', 'approval_request', ['active_dedupe_key'])


def downgrade() -> None:
    op.drop_constraint('uq_approval_active_dedupe', 'approval_request', type_='unique')
    op.drop_column('approval_request', 'active_dedupe_key')
