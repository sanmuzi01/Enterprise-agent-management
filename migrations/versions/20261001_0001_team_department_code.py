"""部门工作台整合：给 teams 加 department_code（部门业务类型），
docs 审查报告指出"业务模块依赖 Agent 发布状态"——这张表解耦了这个判断，
部门工作台显示哪个业务模块改成直接读这个字段，不再靠"有没有已发布的对应
Agent"反推。

值域跟 service/runtime/central_router.py::VALID_DEPARTMENT_CODES 一致
（hr/procurement/sales/finance/it），校验在应用层做，不在数据库加 CHECK
约束（跟这个项目其它地方的既有风格一致）。不加唯一约束——允许多个部门共享
同一个 department_code（比如"销售一部"/"销售二部"都是 sales），这是故意的：
它们暂时只能共享同一个已发布的 CRM Agent（agent.department_publish_key
全局唯一约束没有动），但部门工作台本身的业务表单可用性不受这个限制。

Revision ID: 20261001_0001
Revises: 20260929_0001
Create Date: 2026-10-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20261001_0001'
down_revision: Union[str, Sequence[str], None] = '20260929_0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('teams', sa.Column('department_code', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('teams', 'department_code')
