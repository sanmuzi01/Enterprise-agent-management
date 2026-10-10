"""CRM Copilot：客户活动、客户别名映射、客户摘要快照、商机变化记录、风险、下一步建议、邮箱连接。

Revision ID: 20261011_0001
Revises: 20261010_0002
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "20261011_0001"
down_revision = "20261010_0002"
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "customer_activity" not in tables:
        op.create_table(
            "customer_activity",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("customer_id", sa.Integer(), nullable=True),
            sa.Column("activity_type", sa.String(20), nullable=False),
            sa.Column("source_provider", sa.String(20), nullable=False),
            sa.Column("external_source_id", sa.String(255), nullable=False),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.Column("participants_json", sa.Text(), nullable=True),
            sa.Column("title", sa.String(300), nullable=True),
            sa.Column("content", mysql.LONGTEXT(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=False),
            sa.Column("trace_id", sa.String(64), nullable=True),
            sa.Column("match_status", sa.String(20), nullable=False),
            sa.Column("match_confidence", sa.Float(), nullable=True),
            sa.Column("match_method", sa.String(30), nullable=True),
            sa.Column("match_candidates_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("source_provider", "external_source_id", name="uq_customer_activity_source"),
        )
        op.create_index("idx_customer_activity_customer", "customer_activity", ["customer_id", "occurred_at"])
        op.create_index("idx_customer_activity_team_match", "customer_activity", ["team_id", "match_status"])
    if "crm_customer_alias" not in tables:
        op.create_table(
            "crm_customer_alias",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("alias_type", sa.String(20), nullable=False),
            sa.Column("alias_value", sa.String(255), nullable=False),
            sa.Column("customer_id", sa.Integer(), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("team_id", "alias_type", "alias_value", name="uq_crm_customer_alias"),
        )
    if "customer_summary_snapshot" not in tables:
        op.create_table(
            "customer_summary_snapshot",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("customer_id", sa.Integer(), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("needs_json", sa.Text(), nullable=True),
            sa.Column("stakeholders_json", sa.Text(), nullable=True),
            sa.Column("risks_json", sa.Text(), nullable=True),
            sa.Column("next_actions_json", sa.Text(), nullable=True),
            sa.Column("based_on_activity_id", sa.Integer(), nullable=True),
            sa.Column("generated_at", sa.DateTime(), nullable=False),
            sa.Column("model_name", sa.String(100), nullable=True),
            sa.Column("generated_by", sa.Integer(), nullable=True),
        )
        op.create_index("idx_customer_summary_customer", "customer_summary_snapshot", ["team_id", "customer_id", "id"])
    if "crm_opportunity_snapshot" not in tables:
        op.create_table(
            "crm_opportunity_snapshot",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("customer_id", sa.Integer(), nullable=False),
            sa.Column("opportunity_id", sa.Integer(), nullable=False),
            sa.Column("stage", sa.String(20), nullable=False),
            sa.Column("amount", sa.String(40), nullable=False),
            sa.Column("expected_close_date", sa.String(10), nullable=True),
            sa.Column("captured_at", sa.DateTime(), nullable=False),
        )
        op.create_index("idx_crm_opp_snapshot", "crm_opportunity_snapshot", ["opportunity_id", "id"])
    if "crm_risk_finding" not in tables:
        op.create_table(
            "crm_risk_finding",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("customer_id", sa.Integer(), nullable=False),
            sa.Column("opportunity_id", sa.Integer(), nullable=True),
            sa.Column("risk_code", sa.String(40), nullable=False),
            sa.Column("level", sa.String(10), nullable=False),
            sa.Column("evidence", sa.Text(), nullable=False),
            sa.Column("suggested_action", sa.String(500), nullable=True),
            sa.Column("source", sa.String(10), nullable=False),
            sa.Column("source_activity_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("dedupe_key", sa.String(200), nullable=False),
            sa.Column("first_seen_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("dedupe_key", name="uq_crm_risk_dedupe"),
        )
        op.create_index("idx_crm_risk_team", "crm_risk_finding", ["team_id", "status"])
    if "crm_action_suggestion" not in tables:
        op.create_table(
            "crm_action_suggestion",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("customer_id", sa.Integer(), nullable=False),
            sa.Column("opportunity_id", sa.Integer(), nullable=True),
            sa.Column("risk_finding_id", sa.Integer(), nullable=True),
            sa.Column("title", sa.String(300), nullable=False),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("due_date", sa.String(10), nullable=True),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("remind_at", sa.DateTime(), nullable=True),
            sa.Column("work_item_key", sa.String(120), nullable=True),
            sa.Column("dedupe_key", sa.String(200), nullable=False),
            sa.Column("decided_by", sa.Integer(), nullable=True),
            sa.Column("decided_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("dedupe_key", name="uq_crm_action_dedupe"),
        )
        op.create_index("idx_crm_action_team", "crm_action_suggestion", ["team_id", "status"])
    if "crm_mail_account" not in tables:
        op.create_table(
            "crm_mail_account",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("imap_host", sa.String(200), nullable=False),
            sa.Column("imap_port", sa.Integer(), nullable=False),
            sa.Column("username", sa.String(200), nullable=False),
            sa.Column("encrypted_password", sa.Text(), nullable=False),
            sa.Column("folder", sa.String(100), nullable=False),
            sa.Column("last_uid", sa.Integer(), nullable=False),
            sa.Column("last_synced_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.String(500), nullable=True),
            sa.Column("enabled", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("user_id", "team_id", name="uq_crm_mail_account"),
        )


def downgrade():
    for table in ("crm_mail_account", "crm_action_suggestion", "crm_risk_finding", "crm_opportunity_snapshot",
                  "customer_summary_snapshot", "crm_customer_alias", "customer_activity"):
        op.drop_table(table)
