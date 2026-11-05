"""Identify fictional seed barbers without changing appointments or real employees."""

from uuid import NAMESPACE_URL, uuid5

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE employees ADD COLUMN is_demo boolean NOT NULL DEFAULT false")
    for name in ("Alex Atelier", "Danylo Atelier"):
        identifier = uuid5(NAMESPACE_URL, "atelier-demo/employee/" + name)
        op.execute(
            f"UPDATE employees SET is_demo = true WHERE id = '{identifier}' AND user_id IS NULL"
        )


def downgrade() -> None:
    op.execute("ALTER TABLE employees DROP COLUMN is_demo")
