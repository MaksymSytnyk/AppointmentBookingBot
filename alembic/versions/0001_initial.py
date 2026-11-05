"""Initial schema, immutable SQL snapshot and database occupancy invariant."""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = [
    "users",
    "services",
    "business_settings",
    "working_hours",
    "holidays",
    "user_roles",
    "employees",
    "admin_audit",
    "employee_services",
    "employee_working_hours",
    "schedule_exceptions",
    "blocked_periods",
    "appointments",
    "exception_intervals",
    "appointment_events",
    "booking_requests",
    "notification_jobs",
    "notification_logs",
]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    sql = Path(__file__).with_name("0001_schema.sql").read_text()
    for statement in sql.split(";"):
        if statement.strip():
            op.execute(statement)
    op.execute(
        "CREATE INDEX ix_blocked_range ON blocked_periods USING gist "
        "(tstzrange(starts_at, ends_at, '[)'))"
    )
    # Append-only audit records even if application code accidentally mutates them.
    op.execute("""CREATE FUNCTION reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Audit records are immutable'; END $$""")
    for table in ("appointment_events", "admin_audit"):
        op.execute(
            f"CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()"
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION reject_audit_mutation()")
    # btree_gist may be shared by other applications; leave the extension installed.
