"""企业统一的模型连接：管理员按服务商配置一次 API Key，全公司共用。

Revision ID: 20261008_0004
Revises: 20261008_0003
"""
from alembic import op
import sqlalchemy as sa

revision = "20261008_0004"
down_revision = "20261008_0003"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("enterprise_llm_connection"):
        op.create_table(
            "enterprise_llm_connection",
            sa.Column("provider", sa.String(30), primary_key=True),
            sa.Column("api_key", sa.Text, nullable=False),
            sa.Column("is_active", sa.Integer, nullable=False),
            sa.Column("updated_by", sa.Integer),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False),
        )


def downgrade():
    op.drop_table("enterprise_llm_connection")
