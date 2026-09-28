"""同一部门同时只能有一个已发布 Agent（P1 并发/正确性修复，
docs/enterprise-rbac-plan.md）。

`agent` 新增 `department_publish_key`（可空）+ 唯一约束。之前
`central_router._find_department_agent` 按 department_code 查所有
lifecycle_status='published' 的部门 Agent，如果有多条（没有任何东西阻止
创建/发布多个同 department_code 的 Agent），选哪一条是未定义行为（没有
唯一约束、没有优先级、没有稳定排序）。这一列由 service/agent_admin_service.py
维护：agent_type='department' 且 lifecycle_status='published' 时等于
department_code 本身，其余状态清空。MySQL 唯一索引允许多个 NULL 共存，只在
非 NULL 值之间强制唯一，约束生效后同一个部门永远最多只有一条 published 记录，
路由不再需要纠结"选哪个"。

Revision ID: 20260928_0002
Revises: 20260928_0001
Create Date: 2026-09-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20260928_0002'
down_revision: Union[str, Sequence[str], None] = '20260928_0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agent', sa.Column('department_publish_key', sa.String(length=20), nullable=True))

    # 防御性数据清洗：如果在加这个约束之前已经存在同一个 department_code 的
    # 多条 published 记录（加约束之前完全没有东西阻止这种情况），先把除了
    # id 最小的那条之外全部打回 draft，不然下面加唯一约束这一步会直接失败。
    # 本机开发库当前这张表是空的（验证过），保留这一步是为了让迁移在真实
    # 存量数据上也能安全跑，不是纸面上假设"应该没事"。
    op.execute("""
        UPDATE agent a
        JOIN (
            SELECT department_code, MIN(id) AS keep_id
            FROM agent
            WHERE agent_type='department' AND lifecycle_status='published' AND department_code IS NOT NULL
            GROUP BY department_code
        ) keep ON a.department_code = keep.department_code
        SET a.lifecycle_status = 'draft'
        WHERE a.agent_type='department' AND a.lifecycle_status='published' AND a.id <> keep.keep_id
    """)

    op.execute("""
        UPDATE agent
        SET department_publish_key = department_code
        WHERE agent_type='department' AND lifecycle_status='published'
    """)

    op.create_unique_constraint('uq_agent_department_publish', 'agent', ['department_publish_key'])


def downgrade() -> None:
    op.drop_constraint('uq_agent_department_publish', 'agent', type_='unique')
    op.drop_column('agent', 'department_publish_key')
