"""考勤：规则、工作日历、名字对应、导入记录、打卡、异常。

Revision ID: 20261007_0006
Revises: 20261007_0005
"""
from alembic import op
import sqlalchemy as sa

revision = "20261007_0006"
down_revision = "20261007_0005"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("attendance_rule"):
        op.create_table(
            "attendance_rule",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("organization_id", sa.Integer, nullable=False),
            sa.Column("team_id", sa.Integer),
            sa.Column("team_key", sa.Integer, nullable=False),
            sa.Column("work_start", sa.String(5), nullable=False),
            sa.Column("work_end", sa.String(5), nullable=False),
            sa.Column("grace_minutes", sa.Integer, nullable=False),
            sa.Column("updated_by", sa.Integer),
            sa.Column("updated_at", sa.DateTime, nullable=False),
            sa.UniqueConstraint("organization_id", "team_key", name="uq_attendance_rule"),
        )
    if not inspector.has_table("attendance_calendar"):
        op.create_table(
            "attendance_calendar",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("organization_id", sa.Integer, nullable=False),
            sa.Column("day", sa.DateTime, nullable=False),
            sa.Column("kind", sa.String(10), nullable=False),
            sa.Column("note", sa.String(60)),
            sa.UniqueConstraint("organization_id", "day", name="uq_attendance_calendar"),
        )
    if not inspector.has_table("attendance_alias"):
        op.create_table(
            "attendance_alias",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("organization_id", sa.Integer, nullable=False),
            sa.Column("alias", sa.String(80), nullable=False),
            sa.Column("user_id", sa.Integer, nullable=False),
            sa.Column("created_by", sa.Integer),
            sa.UniqueConstraint("organization_id", "alias", name="uq_attendance_alias"),
        )
    if not inspector.has_table("attendance_import"):
        op.create_table(
            "attendance_import",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("organization_id", sa.Integer, nullable=False),
            sa.Column("uploaded_by", sa.Integer, nullable=False),
            sa.Column("file_name", sa.String(255), nullable=False),
            sa.Column("source_format", sa.String(20), nullable=False),
            sa.Column("period_start", sa.DateTime),
            sa.Column("period_end", sa.DateTime),
            sa.Column("row_count", sa.Integer, nullable=False),
            sa.Column("punch_count", sa.Integer, nullable=False),
            sa.Column("new_punch_count", sa.Integer, nullable=False),
            sa.Column("unmatched_json", sa.Text),
            sa.Column("skipped_json", sa.Text),
            sa.Column("created_at", sa.DateTime, nullable=False),
        )
    if not inspector.has_table("attendance_punch"):
        op.create_table(
            "attendance_punch",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("organization_id", sa.Integer, nullable=False),
            sa.Column("user_id", sa.Integer, nullable=False),
            sa.Column("punch_at", sa.DateTime, nullable=False),
            sa.Column("import_id", sa.Integer),
            sa.UniqueConstraint("user_id", "punch_at", name="uq_attendance_punch"),
        )
        op.create_index("idx_attendance_punch_org_day", "attendance_punch", ["organization_id", "punch_at"])
    if not inspector.has_table("attendance_anomaly"):
        op.create_table(
            "attendance_anomaly",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("organization_id", sa.Integer, nullable=False),
            sa.Column("team_id", sa.Integer),
            sa.Column("user_id", sa.Integer, nullable=False),
            sa.Column("work_date", sa.DateTime, nullable=False),
            sa.Column("type", sa.String(20), nullable=False),
            sa.Column("severity", sa.String(8), nullable=False),
            sa.Column("detail_json", sa.Text, nullable=False),
            sa.Column("status", sa.String(12), nullable=False),
            sa.Column("explanation", sa.String(500)),
            sa.Column("explained_at", sa.DateTime),
            sa.Column("decided_by", sa.Integer),
            sa.Column("decision_note", sa.String(500)),
            sa.Column("decided_at", sa.DateTime),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False),
            sa.UniqueConstraint("user_id", "work_date", "type", name="uq_attendance_anomaly"),
        )
        op.create_index("idx_attendance_anomaly_team", "attendance_anomaly", ["team_id", "status", "work_date"])


def downgrade():
    for table in ("attendance_anomaly", "attendance_punch", "attendance_import", "attendance_alias", "attendance_calendar", "attendance_rule"):
        op.drop_table(table)
