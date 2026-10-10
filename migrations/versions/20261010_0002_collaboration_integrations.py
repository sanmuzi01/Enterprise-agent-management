"""统一外部接入层（飞书 / 钉钉）：应用配置、人员与部门映射、入站事件去重。

Revision ID: 20261010_0002
Revises: 20261010_0001
"""
from alembic import op
import sqlalchemy as sa

revision = "20261010_0002"
down_revision = "20261010_0001"
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "collaboration_app" not in tables:
        op.create_table(
            "collaboration_app",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(20), nullable=False),
            sa.Column("app_id", sa.String(120), nullable=False),
            sa.Column("encrypted_app_secret", sa.Text(), nullable=False),
            sa.Column("verification_token", sa.String(200), nullable=True),
            sa.Column("encrypted_encrypt_key", sa.Text(), nullable=True),
            sa.Column("robot_code", sa.String(120), nullable=True),
            sa.Column("card_template_id", sa.String(120), nullable=True),
            sa.Column("enabled", sa.Integer(), nullable=False),
            sa.Column("last_health_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.String(500), nullable=True),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("organization_id", "provider", name="uq_collaboration_app"),
        )
    if "external_user_binding" not in tables:
        op.create_table(
            "external_user_binding",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(20), nullable=False),
            sa.Column("external_tenant_id", sa.String(120), nullable=False),
            sa.Column("external_user_id", sa.String(120), nullable=False),
            sa.Column("external_union_id", sa.String(120), nullable=True),
            sa.Column("external_name", sa.String(120), nullable=True),
            sa.Column("local_user_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("last_synced_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("provider", "external_tenant_id", "external_user_id", name="uq_external_user"),
        )
        op.create_index("idx_external_user_local", "external_user_binding", ["local_user_id"])
    if "external_department_binding" not in tables:
        op.create_table(
            "external_department_binding",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(20), nullable=False),
            sa.Column("external_tenant_id", sa.String(120), nullable=False),
            sa.Column("external_department_id", sa.String(120), nullable=False),
            sa.Column("external_name", sa.String(200), nullable=True),
            sa.Column("external_parent_id", sa.String(120), nullable=True),
            sa.Column("local_team_id", sa.Integer(), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("provider", "external_tenant_id", "external_department_id", name="uq_external_department"),
        )
    if "external_event_inbox" not in tables:
        op.create_table(
            "external_event_inbox",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("provider", sa.String(20), nullable=False),
            sa.Column("tenant_id", sa.String(120), nullable=False),
            sa.Column("event_id", sa.String(120), nullable=False),
            sa.Column("event_type", sa.String(80), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("received_at", sa.DateTime(), nullable=False),
            sa.Column("processed_at", sa.DateTime(), nullable=True),
            sa.Column("trace_id", sa.String(64), nullable=True),
            sa.Column("last_error", sa.String(500), nullable=True),
            sa.UniqueConstraint("provider", "tenant_id", "event_id", name="uq_external_event"),
        )
        op.create_index("idx_external_event_status", "external_event_inbox", ["status", "received_at"])


def downgrade():
    op.drop_index("idx_external_event_status", table_name="external_event_inbox")
    op.drop_table("external_event_inbox")
    op.drop_table("external_department_binding")
    op.drop_index("idx_external_user_local", table_name="external_user_binding")
    op.drop_table("external_user_binding")
    op.drop_table("collaboration_app")
