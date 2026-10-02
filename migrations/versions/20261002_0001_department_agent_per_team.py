"""部门 Agent 发布唯一性改为按部门（team）+ 回填部门业务类型。

1. `teams.department_code` 为空、但该部门绑定的部门 Agent（非退役）只有一种业务方向时，
   用它回填——这些部门是在"部门业务类型"字段出现之前，靠手工建 Agent 定下业务方向的。
2. `agent.department_publish_key` 原来等于 department_code（全公司每种业务只能有一个
   已发布 Agent），改为 "t{team_id}"（每个部门一个已发布主 Agent）。同一部门如有多个
   已发布 Agent，保留 id 最小的，其余退回 draft，保证唯一约束成立。

Revision ID: 20261002_0001
Revises: 20261001_0002
"""
from alembic import op

revision = "20261002_0001"
down_revision = "20261001_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        UPDATE teams t
        JOIN (
            SELECT team_id, MIN(department_code) AS code
            FROM agent
            WHERE agent_type='department' AND lifecycle_status <> 'retired'
              AND team_id IS NOT NULL AND department_code IS NOT NULL
            GROUP BY team_id
            HAVING COUNT(DISTINCT department_code) = 1
        ) only_code ON only_code.team_id = t.id
        SET t.department_code = only_code.code
        WHERE t.department_code IS NULL
    """)
    op.execute("UPDATE agent SET department_publish_key = NULL WHERE department_publish_key IS NOT NULL")
    op.execute("""
        UPDATE agent a
        JOIN (
            SELECT team_id, MIN(id) AS keep_id FROM agent
            WHERE agent_type='department' AND lifecycle_status='published' AND team_id IS NOT NULL
            GROUP BY team_id
        ) keep ON a.team_id = keep.team_id
        SET a.lifecycle_status = 'draft'
        WHERE a.agent_type='department' AND a.lifecycle_status='published' AND a.id <> keep.keep_id
    """)
    op.execute("""
        UPDATE agent SET department_publish_key = CONCAT('t', team_id)
        WHERE agent_type='department' AND lifecycle_status='published' AND team_id IS NOT NULL
    """)


def downgrade():
    op.execute("UPDATE agent SET department_publish_key = NULL WHERE department_publish_key IS NOT NULL")
    op.execute("""
        UPDATE agent a
        JOIN (
            SELECT department_code, MIN(id) AS keep_id FROM agent
            WHERE agent_type='department' AND lifecycle_status='published' AND department_code IS NOT NULL
            GROUP BY department_code
        ) keep ON a.department_code = keep.department_code
        SET a.lifecycle_status = 'draft'
        WHERE a.agent_type='department' AND a.lifecycle_status='published' AND a.id <> keep.keep_id
    """)
    op.execute("""
        UPDATE agent SET department_publish_key = department_code
        WHERE agent_type='department' AND lifecycle_status='published' AND department_code IS NOT NULL
    """)
