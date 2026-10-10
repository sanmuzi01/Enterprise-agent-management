"""财务与 IT 补齐：发票识别结果、费用标准规则、ERP 推送记录、IT 自助会话与文章反馈。

Revision ID: 20261011_0002
Revises: 20261011_0001
"""
from alembic import op
import sqlalchemy as sa

revision = "20261011_0002"
down_revision = "20261011_0001"
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "invoice_extraction" not in tables:
        op.create_table(
            "invoice_extraction",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("file_name", sa.String(255), nullable=True),
            sa.Column("file_sha256", sa.String(64), nullable=False),
            sa.Column("invoice_type", sa.String(40), nullable=True),
            sa.Column("invoice_code", sa.String(20), nullable=True),
            sa.Column("invoice_number", sa.String(30), nullable=True),
            sa.Column("issued_at", sa.String(10), nullable=True),
            sa.Column("seller_name", sa.String(200), nullable=True),
            sa.Column("seller_tax_id", sa.String(30), nullable=True),
            sa.Column("buyer_name", sa.String(200), nullable=True),
            sa.Column("buyer_tax_id", sa.String(30), nullable=True),
            sa.Column("amount_without_tax", sa.String(20), nullable=True),
            sa.Column("tax_amount", sa.String(20), nullable=True),
            sa.Column("total_amount", sa.String(20), nullable=True),
            sa.Column("currency", sa.String(10), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("field_confidence_json", sa.Text(), nullable=True),
            sa.Column("checks_json", sa.Text(), nullable=True),
            sa.Column("corrected_fields_json", sa.Text(), nullable=True),
            sa.Column("raw_reference", sa.Text(), nullable=True),
            sa.Column("method", sa.String(20), nullable=True),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("claim_id", sa.Integer(), nullable=True),
            sa.Column("confirmed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("idx_invoice_extraction_user", "invoice_extraction", ["user_id", "id"])
        op.create_index("idx_invoice_extraction_hash", "invoice_extraction", ["file_sha256"])
        op.create_index("idx_invoice_extraction_number", "invoice_extraction", ["invoice_number"])
    if "expense_policy_rule" not in tables:
        op.create_table(
            "expense_policy_rule",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("category", sa.String(20), nullable=False),
            sa.Column("city_level", sa.String(10), nullable=True),
            sa.Column("employee_level", sa.String(20), nullable=True),
            sa.Column("amount_limit", sa.String(20), nullable=True),
            sa.Column("receipt_required", sa.Integer(), nullable=False),
            sa.Column("approval_level", sa.String(20), nullable=False),
            sa.Column("effective_from", sa.String(10), nullable=True),
            sa.Column("effective_to", sa.String(10), nullable=True),
            sa.Column("note", sa.String(300), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("idx_expense_policy_org", "expense_policy_rule", ["organization_id", "category"])
    if "erp_export" not in tables:
        op.create_table(
            "erp_export",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("voucher_id", sa.Integer(), nullable=False),
            sa.Column("idempotency_key", sa.String(80), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("erp_document_id", sa.String(120), nullable=True),
            sa.Column("last_error", sa.String(500), nullable=True),
            sa.Column("requested_by", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("voucher_id", name="uq_erp_export_voucher"),
        )
    if "it_self_service_session" not in tables:
        op.create_table(
            "it_self_service_session",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("team_id", sa.Integer(), nullable=True),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("classification", sa.String(30), nullable=True),
            sa.Column("article_ids_json", sa.Text(), nullable=True),
            sa.Column("suggestion", sa.Text(), nullable=True),
            sa.Column("confirmed_solved", sa.Integer(), nullable=True),
            sa.Column("solved_article_id", sa.Integer(), nullable=True),
            sa.Column("converted_ticket_id", sa.Integer(), nullable=True),
            sa.Column("reopened", sa.Integer(), nullable=False),
            sa.Column("reopened_at", sa.DateTime(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("feedback_at", sa.DateTime(), nullable=True),
        )
        op.create_index("idx_it_session_user", "it_self_service_session", ["user_id", "started_at"])
        op.create_index("idx_it_session_ticket", "it_self_service_session", ["converted_ticket_id"])
    if "it_article_feedback" not in tables:
        op.create_table(
            "it_article_feedback",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("session_id", sa.Integer(), nullable=False),
            sa.Column("article_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("viewed_at", sa.DateTime(), nullable=True),
            sa.Column("helpful", sa.Integer(), nullable=True),
            sa.Column("rated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("session_id", "article_id", name="uq_it_article_feedback"),
        )
        op.create_index("idx_it_article_feedback_article", "it_article_feedback", ["article_id"])


def downgrade():
    for table in ("it_article_feedback", "it_self_service_session", "erp_export", "expense_policy_rule", "invoice_extraction"):
        op.drop_table(table)
