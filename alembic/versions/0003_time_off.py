"""Independent employee availability permissions and owner-reviewed time off."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE employees ADD COLUMN may_block_periods boolean NOT NULL DEFAULT false")
    op.execute("UPDATE employees SET may_block_periods = may_manage_availability")
    op.execute(
        "ALTER TABLE employees ADD COLUMN may_request_time_off boolean NOT NULL DEFAULT false"
    )
    op.execute("""CREATE TABLE time_off_requests (
        id uuid CONSTRAINT pk_time_off_requests PRIMARY KEY,
        employee_id uuid NOT NULL CONSTRAINT fk_time_off_requests_employee_id_employees
            REFERENCES employees(id),
        starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL,
        reason varchar(300) NOT NULL, status varchar(16) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ck_time_off_requests_interval CHECK (ends_at > starts_at),
        CONSTRAINT ck_time_off_requests_status CHECK (status IN ('pending','approved','rejected'))
    )""")
    op.execute("CREATE INDEX ix_time_off_requests_employee_id ON time_off_requests(employee_id)")


def downgrade() -> None:
    op.execute("DROP TABLE time_off_requests")
    op.execute("ALTER TABLE employees DROP COLUMN may_request_time_off")
    op.execute("ALTER TABLE employees DROP COLUMN may_block_periods")
