"""事务性发件箱 / 消费者收件箱 / 重试租约 / 死信。

Revision ID: 20261007_0003
Revises: 20261007_0002
"""
from alembic import op
import sqlalchemy as sa

revision = "20261007_0003"
down_revision = "20261007_0002"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("outbox_event"):
        op.create_table(
            "outbox_event",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("event_id", sa.String(36), nullable=False),
            sa.Column("topic", sa.String(80), nullable=False),
            sa.Column("event_key", sa.String(64)),
            sa.Column("event_type", sa.String(60), nullable=False),
            sa.Column("schema_version", sa.Integer, nullable=False),
            sa.Column("aggregate_type", sa.String(40), nullable=False),
            sa.Column("aggregate_id", sa.String(64), nullable=False),
            sa.Column("organization_id", sa.Integer),
            sa.Column("department_id", sa.Integer),
            sa.Column("trace_id", sa.String(64)),
            sa.Column("producer", sa.String(40), nullable=False),
            sa.Column("payload_json", sa.Text, nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("published_at", sa.DateTime),
            sa.Column("publish_attempts", sa.Integer, nullable=False),
            sa.Column("next_publish_at", sa.DateTime),
            sa.Column("last_error", sa.String(300)),
            sa.UniqueConstraint("event_id", name="uq_outbox_event_id"),
        )
        op.create_index("idx_outbox_publish", "outbox_event", ["published_at", "next_publish_at"])
        op.create_index("idx_outbox_topic", "outbox_event", ["topic", "id"])
    if not inspector.has_table("consumer_inbox"):
        op.create_table(
            "consumer_inbox",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("event_id", sa.String(36), nullable=False),
            sa.Column("consumer", sa.String(60), nullable=False),
            sa.Column("processed_at", sa.DateTime, nullable=False),
            sa.UniqueConstraint("event_id", "consumer", name="uq_inbox_event_consumer"),
        )
    if not inspector.has_table("consumer_retry"):
        op.create_table(
            "consumer_retry",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("event_id", sa.String(36), nullable=False),
            sa.Column("consumer", sa.String(60), nullable=False),
            sa.Column("attempts", sa.Integer, nullable=False),
            sa.Column("next_attempt_at", sa.DateTime, nullable=False),
            sa.Column("last_error", sa.String(300)),
            sa.UniqueConstraint("event_id", "consumer", name="uq_retry_event_consumer"),
        )
    if not inspector.has_table("dead_letter"):
        op.create_table(
            "dead_letter",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("event_id", sa.String(36), nullable=False),
            sa.Column("consumer", sa.String(60), nullable=False),
            sa.Column("topic", sa.String(80), nullable=False),
            sa.Column("event_type", sa.String(60), nullable=False),
            sa.Column("trace_id", sa.String(64)),
            sa.Column("payload_json", sa.Text, nullable=False),
            sa.Column("error", sa.String(500)),
            sa.Column("attempts", sa.Integer, nullable=False),
            sa.Column("status", sa.String(12), nullable=False),
            sa.Column("discard_reason", sa.String(500)),
            sa.Column("handled_by", sa.Integer),
            sa.Column("handled_at", sa.DateTime),
            sa.Column("created_at", sa.DateTime, nullable=False),
        )
        op.create_index("idx_dead_letter_status", "dead_letter", ["status", "id"])
        op.create_index("idx_dead_letter_event", "dead_letter", ["event_id", "consumer"])


def downgrade():
    for table in ("dead_letter", "consumer_retry", "consumer_inbox", "outbox_event"):
        op.drop_table(table)
