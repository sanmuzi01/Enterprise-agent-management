"""统一提效事实表 productivity_fact；CRM 建议记下销售的选择（原样创建 / 修改后创建）。

Revision ID: 20261011_0003
Revises: 20261011_0002
"""
from alembic import op
import sqlalchemy as sa

revision = "20261011_0003"
down_revision = "20261011_0002"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "productivity_fact" not in set(inspector.get_table_names()):
        op.create_table(
            "productivity_fact",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("source_key", sa.String(120), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=True),
            sa.Column("team_id", sa.Integer(), nullable=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("agent_id", sa.Integer(), nullable=True),
            sa.Column("workflow_type", sa.String(60), nullable=False),
            sa.Column("business_object_type", sa.String(40), nullable=True),
            sa.Column("business_object_id", sa.String(120), nullable=True),
            sa.Column("source_type", sa.String(30), nullable=False),
            sa.Column("baseline_minutes", sa.Float(), nullable=False),
            sa.Column("agent_seconds", sa.Float(), nullable=False),
            sa.Column("review_seconds", sa.Float(), nullable=False),
            sa.Column("saved_minutes", sa.Float(), nullable=False),
            sa.Column("reported_saved_minutes", sa.Float(), nullable=True),
            sa.Column("draft_created", sa.Integer(), nullable=False),
            sa.Column("draft_adopted", sa.Integer(), nullable=False),
            sa.Column("adopted_as_is", sa.Integer(), nullable=False),
            sa.Column("business_initiated", sa.Integer(), nullable=False),
            sa.Column("completed", sa.Integer(), nullable=False),
            sa.Column("failed", sa.Integer(), nullable=False),
            sa.Column("failure_code", sa.String(60), nullable=True),
            sa.Column("fields_changed_json", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("source_key", name="uq_productivity_fact_source"),
        )
        op.create_index("idx_productivity_fact_team", "productivity_fact", ["team_id", "started_at"])
        op.create_index("idx_productivity_fact_org", "productivity_fact", ["organization_id", "started_at"])
    columns = {c["name"] for c in inspector.get_columns("crm_action_suggestion")}
    if "decision" not in columns:
        op.add_column("crm_action_suggestion", sa.Column("decision", sa.String(20), nullable=True))


def downgrade():
    op.drop_column("crm_action_suggestion", "decision")
    op.drop_table("productivity_fact")
