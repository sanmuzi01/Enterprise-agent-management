"""一人一部门：team_members.user_id 改为唯一。

Revision ID: 20261009_0002
Revises: 20261009_0001
"""
from alembic import op
import sqlalchemy as sa


revision = "20261009_0002"
down_revision = "20261009_0001"
branch_labels = None
depends_on = None


def _indexes():
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("team_members")}


def upgrade():
    bind = op.get_bind()
    indexes = _indexes()
    if "uq_team_member_user" not in indexes:
        duplicate = bind.execute(sa.text(
            "SELECT user_id, COUNT(*) AS n FROM team_members GROUP BY user_id HAVING COUNT(*) > 1 LIMIT 1"
        )).first()
        if duplicate:
            raise RuntimeError(
                f"不能启用一人一部门约束：用户 #{duplicate[0]} 仍有 {duplicate[1]} 条部门归属；"
                "请先确认应保留的部门并清理重复数据"
            )

    # 原复合唯一索引同时承担 team_id 外键所需的前缀索引；先补普通 team_id 索引再删除它。
    if "idx_team_member_team" not in indexes:
        op.create_index("idx_team_member_team", "team_members", ["team_id"], unique=False)
    indexes = _indexes()
    # user_id 的旧普通索引承担外键索引职责。MySQL 要求先建新唯一索引，才能删除旧索引。
    if "uq_team_member_user" not in indexes:
        op.create_index("uq_team_member_user", "team_members", ["user_id"], unique=True)
    indexes = _indexes()
    if "uq_team_member" in indexes:
        op.drop_index("uq_team_member", table_name="team_members")
    indexes = _indexes()
    if "idx_team_member_user" in indexes:
        op.drop_index("idx_team_member_user", table_name="team_members")


def downgrade():
    indexes = _indexes()
    if "idx_team_member_user" not in indexes:
        op.create_index("idx_team_member_user", "team_members", ["user_id"], unique=False)
    indexes = _indexes()
    if "uq_team_member" not in indexes:
        op.create_index("uq_team_member", "team_members", ["team_id", "user_id"], unique=True)
    indexes = _indexes()
    if "uq_team_member_user" in indexes:
        op.drop_index("uq_team_member_user", table_name="team_members")
    indexes = _indexes()
    if "idx_team_member_team" in indexes:
        op.drop_index("idx_team_member_team", table_name="team_members")
