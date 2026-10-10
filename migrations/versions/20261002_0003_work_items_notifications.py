"""统一待办、站内通知、通知偏好、提醒规则运行状态。

Revision ID: 20261002_0003
Revises: 20261002_0002
"""
from alembic import op
import sqlalchemy as sa

revision = "20261002_0003"
down_revision = "20261002_0002"
branch_labels = None
depends_on = None


def _missing(name):
    return not sa.inspect(op.get_bind()).has_table(name)


def upgrade():
    if _missing("work_item"):
        op.create_table(
            "work_item",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id", name="fk_work_item_user"), nullable=False),
            sa.Column("team_id", sa.Integer, sa.ForeignKey("teams.id", name="fk_work_item_team"), nullable=True),
            sa.Column("source_type", sa.String(30), nullable=False),
            sa.Column("source_key", sa.String(120), nullable=False),
            sa.Column("rule", sa.String(40), nullable=True),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("detail", sa.String(500), nullable=True),
            sa.Column("link", sa.String(200), nullable=True),
            sa.Column("priority", sa.String(10), nullable=False),
            sa.Column("status", sa.String(12), nullable=False),
            sa.Column("due_at", sa.DateTime, nullable=True),
            sa.Column("resolved_by", sa.String(12), nullable=True),
            sa.Column("completed_at", sa.DateTime, nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False),
            sa.UniqueConstraint("user_id", "source_key", name="uq_work_item_source"),
        )
        op.create_index("idx_work_item_owner_status", "work_item", ["user_id", "status", "due_at"])
    if _missing("notification"):
        op.create_table(
            "notification",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id", name="fk_notification_user"), nullable=False),
            sa.Column("category", sa.String(30), nullable=False),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("body", sa.String(500), nullable=True),
            sa.Column("link", sa.String(200), nullable=True),
            sa.Column("dedupe_key", sa.String(160), nullable=False),
            sa.Column("read_at", sa.DateTime, nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.UniqueConstraint("user_id", "dedupe_key", name="uq_notification_dedupe"),
        )
        op.create_index("idx_notification_user_read", "notification", ["user_id", "read_at", "created_at"])
    if _missing("notification_preference"):
        op.create_table(
            "notification_preference",
            sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id", name="fk_notification_pref_user"),
                      primary_key=True),
            sa.Column("muted_categories", sa.Text, nullable=False),
            sa.Column("quiet_start", sa.String(5), nullable=True),
            sa.Column("quiet_end", sa.String(5), nullable=True),
            sa.Column("push_external", sa.Integer, nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime, nullable=False),
        )
    if _missing("reminder_run"):
        op.create_table(
            "reminder_run",
            sa.Column("rule", sa.String(40), primary_key=True),
            sa.Column("lease_until", sa.DateTime, nullable=True),
            sa.Column("next_run_at", sa.DateTime, nullable=True),
            sa.Column("last_started_at", sa.DateTime, nullable=True),
            sa.Column("last_finished_at", sa.DateTime, nullable=True),
            sa.Column("last_status", sa.String(12), nullable=True),
            sa.Column("last_error", sa.String(500), nullable=True),
            sa.Column("last_created", sa.Integer, nullable=False, server_default="0"),
            sa.Column("last_resolved", sa.Integer, nullable=False, server_default="0"),
            sa.Column("consecutive_failures", sa.Integer, nullable=False, server_default="0"),
        )


def downgrade():
    for table in ("reminder_run", "notification_preference", "notification", "work_item"):
        op.drop_table(table)
