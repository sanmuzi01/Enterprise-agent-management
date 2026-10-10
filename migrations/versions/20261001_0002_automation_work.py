"""Persist reviewable CRM/expense work products and execution measurements."""
from alembic import op
import sqlalchemy as sa

revision = "20261001_0002"
down_revision = "20261001_0001"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("automation_work"):
        return
    op.create_table(
        "automation_work",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("team_id", sa.Integer, sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("request_key", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("sensitivity", sa.String(20), nullable=False),
        sa.Column("source_text", sa.Text, nullable=False),
        sa.Column("customer_id", sa.Integer),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("proposal_json", sa.Text),
        sa.Column("accepted_json", sa.Text),
        sa.Column("business_result_json", sa.Text),
        sa.Column("completed_tasks_json", sa.Text, nullable=False),
        sa.Column("error_message", sa.String(300)),
        sa.Column("elapsed_ms", sa.Integer, nullable=False),
        sa.Column("total_tokens", sa.Integer),
        sa.Column("edited", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("user_id", "request_key", name="uq_automation_request"),
    )
    op.create_index("idx_automation_owner_team", "automation_work", ["user_id", "team_id", "created_at"])


def downgrade():
    op.drop_table("automation_work")
