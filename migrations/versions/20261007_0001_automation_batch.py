"""AI 工作成果：批量整理（同一批次的多份材料在后台依次整理，可查看进度）。

Revision ID: 20261007_0001
Revises: 20261006_0001
"""
from alembic import op
import sqlalchemy as sa

revision = "20261007_0001"
down_revision = "20261006_0001"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("automation_work")}
    if "batch_id" not in columns:
        op.add_column("automation_work", sa.Column("batch_id", sa.String(36), nullable=True))
        op.add_column("automation_work", sa.Column("batch_name", sa.String(255), nullable=True))
        op.add_column("automation_work", sa.Column("batch_index", sa.Integer, nullable=True))
        op.create_index("idx_automation_batch", "automation_work", ["batch_id"])


def downgrade():
    op.drop_index("idx_automation_batch", table_name="automation_work")
    op.drop_column("automation_work", "batch_index")
    op.drop_column("automation_work", "batch_name")
    op.drop_column("automation_work", "batch_id")
