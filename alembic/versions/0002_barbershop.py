"""Barbershop roles, secure invitations and employee permissions; preserve history."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE user_roles DROP CONSTRAINT ck_user_roles_role")
    op.execute(
        "UPDATE user_roles SET role = CASE role WHEN 'admin' THEN 'OWNER' "
        "WHEN 'employee' THEN 'BARBER' ELSE role END"
    )
    op.execute(
        "INSERT INTO user_roles (user_id, role) SELECT id, 'CUSTOMER' FROM users "
        "ON CONFLICT DO NOTHING"
    )
    op.execute(
        "ALTER TABLE user_roles ADD CONSTRAINT ck_user_roles_role "
        "CHECK (role IN ('CUSTOMER','BARBER','OWNER'))"
    )
    op.execute("UPDATE users SET locale = 'uk'")
    op.execute(
        "ALTER TABLE employees ADD COLUMN may_cancel_appointments boolean NOT NULL DEFAULT false"
    )
    op.execute("""CREATE TABLE employee_invitations (
        id uuid CONSTRAINT pk_employee_invitations PRIMARY KEY,
        employee_id uuid NOT NULL CONSTRAINT fk_employee_invitations_employee_id_employees
        REFERENCES employees(id),
        token_hash varchar(64) NOT NULL CONSTRAINT uq_employee_invitations_token_hash UNIQUE,
        expires_at timestamptz NOT NULL,
        used_at timestamptz, target_telegram_id bigint,
        created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute(
        "CREATE INDEX ix_employee_invitations_employee_id ON employee_invitations(employee_id)"
    )
    op.execute(
        "UPDATE business_settings SET name = 'ATELIER BARBER' "
        "WHERE name IN ('Atelier Barber', 'ATELIER BARBER')"
    )
    op.execute(
        "UPDATE business_settings SET about = "
        "'Точна стрижка, догляд за бородою та увага до деталей.' "
        "WHERE about = 'Premium grooming, thoughtfully delivered.'"
    )
    op.execute(
        "UPDATE business_settings SET contacts = 'Контакти ще не додано.' "
        "WHERE contacts = 'Configure business contact information.'"
    )

    op.execute(
        "UPDATE business_settings SET about = 'Стрижки та догляд з увагою до деталей.' "
        "WHERE about = 'Atelier Barber · Precision grooming and considered personal service.'"
    )
    op.execute(
        "UPDATE business_settings SET contacts = "
        "'Демонстраційний барбершоп. Контакти ще не налаштовано.' "
        "WHERE contacts = "
        "'Fictional portfolio business — configure real contact details before use.'"
    )
    op.execute(
        "UPDATE employees SET name = CASE name WHEN 'Alex Atelier' THEN 'Олександр' "
        "WHEN 'Danylo Atelier' THEN 'Данило' ELSE name END, "
        "bio = 'Стрижки та догляд за бородою. Демонстраційний профіль.' "
        "WHERE bio = 'Fictional demo stylist'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE employee_invitations")
    op.execute("ALTER TABLE employees DROP COLUMN may_cancel_appointments")
    op.execute("ALTER TABLE user_roles DROP CONSTRAINT ck_user_roles_role")
    op.execute("DELETE FROM user_roles WHERE role = 'CUSTOMER'")
    op.execute(
        "UPDATE user_roles SET role = CASE role WHEN 'OWNER' THEN 'admin' ELSE 'employee' END"
    )
    op.execute(
        "ALTER TABLE user_roles ADD CONSTRAINT ck_user_roles_role "
        "CHECK (role IN ('admin','employee'))"
    )
