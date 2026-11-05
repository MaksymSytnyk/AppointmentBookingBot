import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, time
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from appointment_booking.app.database.models import (
    BusinessSettings,
    Employee,
    EmployeeService,
    Service,
    User,
    UserRole,
    WorkingHours,
)
from appointment_booking.app.database.session import create_database

NOW = datetime(2030, 1, 6, 0, tzinfo=UTC)
START = datetime(2030, 1, 7, 10, tzinfo=UTC)


@dataclass
class Demo:
    sessions: object
    customer: UUID
    other: UUID
    admin: UUID
    employee_user: UUID
    employee: UUID
    employee2: UUID
    service: UUID


@pytest.fixture(scope="session")
def test_url():
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url:
        pytest.fail("TEST_DATABASE_URL is required for integration tests (never silently skipped)")
    parsed = make_url(url)
    if not parsed.database or not parsed.database.endswith("_test"):
        pytest.fail("Destructive tests require a dedicated database name ending in _test")
    env = {**os.environ, "DATABASE_URL": url}
    result = subprocess.run(["alembic", "upgrade", "head"], env=env, capture_output=True)
    if result.returncode:
        pytest.fail(
            "Test database migration failed; run alembic with TEST_DATABASE_URL to diagnose"
        )
    return url


@pytest.fixture
async def demo(test_url):
    engine, sessions = create_database(test_url)
    async with sessions.begin() as session:
        await session.execute(
            text("TRUNCATE users, services, business_settings, working_hours, holidays CASCADE")
        )
        users = [User(telegram_id=100 + i, display_name=f"Test {i}", locale="uk") for i in range(4)]
        session.add_all(users)
        session.add(
            BusinessSettings(
                timezone="UTC", lead_minutes=0, cancellation_hours=12, reminder_hours=[24, 2]
            )
        )
        service = Service(
            name={"en": "Cut", "uk": "Стрижка"},
            description={"en": "Cut", "uk": "Стрижка"},
            duration_minutes=60,
            buffer_before=0,
            buffer_after=15,
            price_minor=80000,
            currency="UAH",
        )
        session.add(service)
        for weekday in range(7):
            session.add(WorkingHours(weekday=weekday, opens=time(9), closes=time(18)))
        await session.flush()
        employees = [
            Employee(
                name=f"Employee {i}",
                user_id=users[3].id if i == 0 else None,
                may_manage_availability=True,
                may_block_periods=True,
                may_request_time_off=True,
            )
            for i in range(2)
        ]
        session.add_all(employees)
        session.add(UserRole(user_id=users[2].id, role="OWNER"))
        session.add(UserRole(user_id=users[3].id, role="BARBER"))
        await session.flush()
        for employee in employees:
            session.add(EmployeeService(employee_id=employee.id, service_id=service.id))
    yield Demo(
        sessions,
        users[0].id,
        users[1].id,
        users[2].id,
        users[3].id,
        employees[0].id,
        employees[1].id,
        service.id,
    )
    await engine.dispose()
