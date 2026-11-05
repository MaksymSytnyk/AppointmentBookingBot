"""The fictional seed cannot create staff accounts or invalidate existing bookings."""

from uuid import NAMESPACE_URL, uuid5

import pytest
from sqlalchemy import select

from appointment_booking.app.core.exceptions import PolicyViolation, ScheduleConflict
from appointment_booking.app.database.models import Employee, EmployeeService, UserRole
from appointment_booking.app.database.repositories import CatalogRepository
from appointment_booking.app.services.admin_service import AdminService
from appointment_booking.app.services.booking_service import BookingService
from scripts.seed import seed
from tests.integration.conftest import NOW
from tests.integration.test_bookings import command

pytestmark = pytest.mark.integration
DEMO_ID = uuid5(NAMESPACE_URL, "atelier-demo/employee/Alex Atelier")
RETIRED_ID = uuid5(NAMESPACE_URL, "atelier-demo/employee/Danylo Atelier")


async def test_repeatable_seed_has_one_explicitly_fictional_barber(demo):
    async with demo.sessions.begin() as session:
        session.add(Employee(id=RETIRED_ID, name="Данило", is_demo=True))
    await seed(demo.sessions)
    await seed(demo.sessions)
    async with demo.sessions() as session:
        fictional = list(
            await session.scalars(select(Employee).where(Employee.is_demo, Employee.active))
        )
        assert len(fictional) == 1
        assert fictional[0].id == DEMO_ID and fictional[0].user_id is None
        assert "не реальний акаунт" in fictional[0].name
        assert "Не реальна людина" in fictional[0].bio
        assert not (await session.get(Employee, RETIRED_ID)).active
        visible = await CatalogRepository(session).employees(active=False)
        assert RETIRED_ID not in {item.id for item in visible}
        # No grants are created by seeding, even for the fictional barber.
        assert len(list(await session.scalars(select(UserRole)))) == 2
    with pytest.raises(PolicyViolation):
        await AdminService(demo.sessions).invite(demo.admin, DEMO_ID)


async def test_seed_never_retires_employee_with_upcoming_appointment(demo):
    async with demo.sessions.begin() as session:
        session.add(Employee(id=RETIRED_ID, name="Данило", is_demo=True))
        await session.flush()
        session.add(EmployeeService(employee_id=RETIRED_ID, service_id=demo.service))
    item = await BookingService(demo.sessions, lambda: NOW).book(
        demo.customer, command(demo, employee_id=RETIRED_ID)
    )
    with pytest.raises(ScheduleConflict) as error:
        await seed(demo.sessions)
    assert item.reference in error.value.references
    async with demo.sessions() as session:
        assert (await session.get(Employee, RETIRED_ID)).active
