"""知识库按部门划分：knowledge_space_departments（空间 ↔ 部门，多对多）。

已经发布到某个部门的空间（scope_type=department 且有 team_id）会自动生成对应的划分记录。

Revision ID: 20261008_0002
Revises: 20261008_0001
"""
from alembic import op
import sqlalchemy as sa

revision = "20261008_0002"
down_revision = "20261008_0001"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("knowledge_space_departments"):
        op.create_table(
            "knowledge_space_departments",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("space_id", sa.Integer, sa.ForeignKey("knowledge_spaces.id", name="fk_ksd_space", ondelete="CASCADE"), nullable=False),
            sa.Column("team_id", sa.Integer, sa.ForeignKey("teams.id", name="fk_ksd_team", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False),
        )
        op.create_index("uq_kspace_department", "knowledge_space_departments", ["space_id", "team_id"], unique=True)
        op.create_index("idx_kspace_department_team", "knowledge_space_departments", ["team_id"])
    op.execute(
        "INSERT INTO knowledge_space_departments (space_id, team_id, created_at) "
        "SELECT id, team_id, NOW() FROM knowledge_spaces "
        "WHERE scope_type = 'department' AND team_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM knowledge_space_departments d WHERE d.space_id = knowledge_spaces.id AND d.team_id = knowledge_spaces.team_id)"
    )


def downgrade():
    op.drop_table("knowledge_space_departments")
