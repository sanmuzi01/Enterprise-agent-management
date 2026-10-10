"""问题中心：system_issue / issue_occurrence / issue_event。

Revision ID: 20261007_0002
Revises: 20261007_0001
"""
from alembic import op
import sqlalchemy as sa

revision = "20261007_0002"
down_revision = "20261007_0001"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("system_issue"):
        op.create_table(
            "system_issue",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("issue_no", sa.String(40)),
            sa.Column("fingerprint", sa.String(64), nullable=False),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("severity", sa.String(10), nullable=False),
            sa.Column("category", sa.String(20), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("service", sa.String(40), nullable=False),
            sa.Column("operation", sa.String(200)),
            sa.Column("error_code", sa.String(60), nullable=False),
            sa.Column("department_id", sa.Integer),
            sa.Column("responsible_user_id", sa.Integer),
            sa.Column("last_trace_id", sa.String(64)),
            sa.Column("sentry_event_id", sa.String(64)),
            sa.Column("affected_resource_type", sa.String(40)),
            sa.Column("affected_resource_id", sa.String(64)),
            sa.Column("occurrence_count", sa.Integer, nullable=False),
            sa.Column("retryable", sa.Integer, nullable=False),
            sa.Column("first_seen_at", sa.DateTime, nullable=False),
            sa.Column("last_seen_at", sa.DateTime, nullable=False),
            sa.Column("acknowledged_at", sa.DateTime),
            sa.Column("resolved_at", sa.DateTime),
            sa.Column("root_cause", sa.Text),
            sa.Column("resolution", sa.Text),
            sa.Column("fix_version", sa.String(80)),
            sa.Column("resolved_by", sa.Integer),
            sa.Column("verified_by", sa.Integer),
            sa.Column("verified_at", sa.DateTime),
            sa.Column("regress_count", sa.Integer, nullable=False),
            sa.UniqueConstraint("fingerprint", name="uq_system_issue_fingerprint"),
        )
        op.create_index("idx_system_issue_status", "system_issue", ["status", "last_seen_at"])
        op.create_index("idx_system_issue_dept", "system_issue", ["department_id", "status"])
    if not inspector.has_table("issue_occurrence"):
        op.create_table(
            "issue_occurrence",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("issue_id", sa.Integer, nullable=False),
            sa.Column("trace_id", sa.String(64)),
            sa.Column("release", sa.String(80)),
            sa.Column("http_status", sa.Integer),
            sa.Column("message", sa.String(500)),
            sa.Column("detail_json", sa.Text),
            sa.Column("occurred_at", sa.DateTime, nullable=False),
        )
        op.create_index("idx_issue_occ_issue", "issue_occurrence", ["issue_id", "id"])
    if not inspector.has_table("issue_event"):
        op.create_table(
            "issue_event",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("issue_id", sa.Integer, nullable=False),
            sa.Column("actor_user_id", sa.Integer),
            sa.Column("action", sa.String(30), nullable=False),
            sa.Column("note", sa.String(1000)),
            sa.Column("created_at", sa.DateTime, nullable=False),
        )
        op.create_index("idx_issue_event_issue", "issue_event", ["issue_id", "id"])


def downgrade():
    op.drop_table("issue_event")
    op.drop_table("issue_occurrence")
    op.drop_table("system_issue")
