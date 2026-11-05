"""Validate migration SQL with PostgreSQL single-user mode when sockets are unavailable.

This is NOT an Alembic runtime or concurrency test. Creates a temporary cluster;
requires installed initdb/postgres and must run as a non-root user.
"""

import ast
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5


class Recorder:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def migration_sql() -> tuple[list[str], list[str]]:
    directory = Path(__file__).resolve().parents[1] / "alembic/versions"
    upgrades: list[str] = []
    downgrades: list[str] = []
    for migration in sorted(directory.glob("[0-9]*.py")):
        module = ast.parse(migration.read_text())
        selected = [
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef)
            or (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "TABLES"
                    for target in node.targets
                )
            )
        ]
        recorder = Recorder()
        namespace: dict[str, Any] = {
            "Path": Path,
            "__file__": str(migration),
            "op": recorder,
            "NAMESPACE_URL": NAMESPACE_URL,
            "uuid5": uuid5,
        }
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(migration), "exec"), namespace)
        namespace["upgrade"]()
        upgrades.extend(recorder.statements)
        recorder.statements.clear()
        namespace["downgrade"]()
        downgrades = recorder.statements + downgrades
    return upgrades, downgrades


def execute(cluster: Path, statements: list[str]) -> str:
    sql = "\n".join(" ".join(statement.split()) for statement in statements) + "\n"
    result = subprocess.run(
        ["postgres", "--single", "-D", str(cluster), "template1"],
        input=sql,
        text=True,
        capture_output=True,
    )
    return result.stdout + result.stderr


def integrity_cases(cluster: Path) -> None:
    customer, employee, service, appointment, event = (str(uuid4()) for _ in range(5))
    statements = [
        "INSERT INTO users (id,telegram_id,display_name,locale,active) "
        f"VALUES ('{customer}',1,'Demo','uk',true)",
        "INSERT INTO employees (id,name,bio,active,may_manage_availability,revision) "
        f"VALUES ('{employee}','Demo','',true,false,1)",
        "INSERT INTO services (id,name,description,duration_minutes,buffer_before,buffer_after,"
        "price_minor,currency,active,revision) "
        f"VALUES ('{service}', '{{}}', '{{}}',60,0,15,80000,'UAH',true,1)",
    ]

    def insert_booking(
        identifier: str, reference: str, start: str, end: str, occupied_end: str
    ) -> str:
        return (
            "INSERT INTO appointments (id,customer_id,employee_id,service_id,reference,status,"
            "starts_at,ends_at,occupied_start,occupied_end,duration_minutes,buffer_before,"
            "buffer_after,price_minor,currency,service_label,version) "
            f"VALUES ('{identifier}','{customer}','{employee}','{service}','{reference}',"
            f"'confirmed','2030-01-07 {start}+00','2030-01-07 {end}+00',"
            f"'2030-01-07 {start}+00','2030-01-07 {occupied_end}+00',60,0,15,80000,'UAH','{{}}',1)"
        )

    statements.extend(
        [
            insert_booking(appointment, "ORIGINAL", "10:00", "11:00", "11:15"),
            insert_booking(str(uuid4()), "CONFLICT", "11:00", "12:00", "12:15"),
            f"UPDATE appointments SET status='completed' WHERE id='{appointment}'",
            insert_booking(str(uuid4()), "BUFFER", "11:00", "12:00", "12:15"),
            f"UPDATE appointments SET status='cancelled' WHERE id='{appointment}'",
            insert_booking(str(uuid4()), "REPLACEMENT", "11:00", "12:00", "12:15"),
            f"UPDATE services SET duration_minutes=0 WHERE id='{service}'",
            "INSERT INTO appointment_events (id,appointment_id,actor_id,action,version,details) "
            f"VALUES ('{event}','{appointment}','{customer}','created',1,'{{}}')",
            f"UPDATE appointment_events SET action='changed' WHERE id='{event}'",
            f"DELETE FROM appointment_events WHERE id='{event}'",
            "SELECT count(*) AS appointment_count FROM appointments",
        ]
    )
    output = execute(cluster, statements)
    assert output.count("ERROR:") == 5, output
    assert output.count('violates exclusion constraint "appointments_no_overlap"') == 2, output
    assert 'violates check constraint "ck_services_duration"' in output, output
    assert output.count("ERROR:  Audit records are immutable") == 2, output
    assert 'appointment_count = "2"' in output, output
    print(
        "PASS buffered overlap, terminal buffer preservation, cancellation release, "
        "duration and audit invariants"
    )


def main() -> None:
    upgrade, downgrade = migration_sql()
    with tempfile.TemporaryDirectory(prefix="atelier-schema-") as directory:
        cluster = Path(directory) / "pg"
        subprocess.run(
            ["initdb", "-D", str(cluster), "-A", "trust", "-U", "atelier"],
            check=True,
            capture_output=True,
        )
        for label, statements in (
            ("upgrade", upgrade),
            ("downgrade", downgrade),
            ("re-upgrade", upgrade),
        ):
            output = execute(cluster, statements)
            if "ERROR:" in output or "FATAL:" in output:
                print(output)
                raise SystemExit(f"{label} SQL failed")
            print(f"PASS {label}: {len(statements)} migration SQL statements")
        integrity_cases(cluster)
    print("Sequential SQL validation only; server-mode and concurrent checks remain required.")


if __name__ == "__main__":
    main()
