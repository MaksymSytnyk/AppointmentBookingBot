"""Product authorization and invitation lifecycle against real PostgreSQL."""

import asyncio
import hashlib
from datetime import time, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from appointment_booking.app.core.exceptions import (
    NotFound,
    PermissionDenied,
    ScheduleConflict,
    SlotUnavailable,
)
from appointment_booking.app.database.models import (
    Appointment,
    Employee,
    EmployeeInvitation,
    UserRole,
)
from appointment_booking.app.domain.dto import EmployeeInput, HoursInput
from appointment_booking.app.services.access import actor_for
from appointment_booking.app.services.admin_service import AdminService
from appointment_booking.app.services.booking_service import BookingService
from tests.integration.conftest import NOW, START
from tests.integration.test_bookings import command

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("who", ["customer", "employee_user"])
async def test_owner_employee_management_is_private(demo, who):
    service = AdminService(demo.sessions, lambda: NOW)
    user = getattr(demo, who)
    for operation in (
        service.employees(user),
        service.save_employee(user, EmployeeInput(name="Чужий барбер")),
        service.invite(user, demo.employee2),
        service.statistics(user, NOW.date(), NOW.date()),
    ):
        with pytest.raises(PermissionDenied):
            await operation


async def test_customer_cannot_open_barber_resources(demo):
    with pytest.raises(PermissionDenied):
        await AdminService(demo.sessions).barber_profile(demo.customer)
    with pytest.raises(PermissionDenied):
        await BookingService(demo.sessions).list(demo.customer, scope="employee")
    with pytest.raises(PermissionDenied):
        await AdminService(demo.sessions).hours(
            demo.customer,
            HoursInput(employee_id=demo.employee, weekday=0, intervals=[(time(9), time(17))]),
        )


async def test_barber_cannot_modify_other_employee(demo):
    with pytest.raises(PermissionDenied):
        await AdminService(demo.sessions).hours(
            demo.employee_user,
            HoursInput(employee_id=demo.employee2, weekday=0, intervals=[(time(9), time(17))]),
        )


async def test_owner_creates_complete_employee_atomically(demo):
    service = AdminService(demo.sessions, lambda: NOW)
    employee = await service.create_employee(
        demo.admin,
        EmployeeInput(name="Олександр", bio="Стрижки та борода"),
        [demo.service],
        [0, 1, 2, 3, 4],
        [(time(9), time(13)), (time(14), time(18))],
    )
    assert employee.user_id is None
    assert await service.assignments(demo.admin, employee.id) == [demo.service]
    assert len(await service.read_hours(demo.admin, employee.id, 0)) == 2
    assert (await service.read_hours(demo.admin, employee.id, 6))[0].closed
    with pytest.raises(NotFound):
        await service.create_employee(
            demo.admin, EmployeeInput(name="Невідомий"), [uuid4()], [0], [(time(9), time(18))]
        )
    async with demo.sessions() as session:
        assert await session.scalar(select(Employee).where(Employee.name == "Невідомий")) is None


async def test_invitation_target_expiry_reuse_and_role(demo):
    service = AdminService(demo.sessions, lambda: NOW)
    token = await service.invite(demo.admin, demo.employee2, 101)
    async with demo.sessions() as session:
        invite = await session.scalar(select(EmployeeInvitation))
        assert invite.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert invite.token_hash != token
    with pytest.raises(PermissionDenied):
        await service.activate_invitation(demo.customer, token)
    with pytest.raises(PermissionDenied):
        await AdminService(demo.sessions, lambda: NOW + timedelta(hours=24)).activate_invitation(
            demo.other, token
        )
    await service.activate_invitation(demo.other, token)
    with pytest.raises(PermissionDenied):
        await service.activate_invitation(demo.customer, token)
    async with demo.sessions() as session:
        actor = await actor_for(session, demo.other)
        assert actor.primary_role == "BARBER"
        assert actor.employee.id == demo.employee2
        assert not actor.admin


async def test_reissued_invitation_revokes_old_link(demo):
    service = AdminService(demo.sessions, lambda: NOW)
    old = await service.invite(demo.admin, demo.employee2)
    new = await service.invite(demo.admin, demo.employee2)
    with pytest.raises(PermissionDenied):
        await service.activate_invitation(demo.other, old)
    await service.activate_invitation(demo.other, new)


async def test_concurrent_invitation_is_single_use(demo):
    service = AdminService(demo.sessions, lambda: NOW)
    token = await service.invite(demo.admin, demo.employee2)
    outcomes = await asyncio.gather(
        *(service.activate_invitation(user, token) for user in (demo.customer, demo.other)),
        return_exceptions=True,
    )
    assert sum(isinstance(item, Employee) for item in outcomes) == 1
    assert sum(isinstance(item, PermissionDenied) for item in outcomes) == 1


async def test_deactivate_preserves_history_and_revokes_employee(demo):
    service = AdminService(demo.sessions, lambda: NOW)
    booking = await BookingService(demo.sessions, lambda: NOW).book(demo.customer, command(demo))
    async with demo.sessions() as session:
        employee = await session.get(Employee, demo.employee)
        revision = employee.revision
    with pytest.raises(ScheduleConflict) as error:
        await service.save_employee(
            demo.admin, EmployeeInput(name="Барбер", active=False), demo.employee, revision
        )
    assert booking.reference in error.value.references
    await BookingService(demo.sessions, lambda: NOW).change(
        demo.customer, booking.id, version=1, action="cancelled"
    )
    await service.save_employee(
        demo.admin, EmployeeInput(name="Барбер", active=False), demo.employee, revision
    )
    async with demo.sessions() as session:
        assert await session.get(Appointment, booking.id) is not None
        assert (await actor_for(session, demo.employee_user)).employee is None
        assert await session.get(UserRole, (demo.employee_user, "BARBER")) is None
    with pytest.raises(PermissionDenied):
        await service.barber_profile(demo.employee_user)
    with pytest.raises(SlotUnavailable):
        await BookingService(demo.sessions, lambda: NOW).book(demo.other, command(demo))


async def test_barber_cancellation_requires_owner_grant_and_assignment(demo):
    service = BookingService(demo.sessions, lambda: NOW)
    booking = await service.book(demo.customer, command(demo))
    with pytest.raises(PermissionDenied):
        await service.change(demo.employee_user, booking.id, version=1, action="cancelled")
    async with demo.sessions.begin() as session:
        employee = await session.get(Employee, demo.employee)
        employee.may_cancel_appointments = True
    changed = await service.change(demo.employee_user, booking.id, version=1, action="cancelled")
    assert changed.status == "cancelled"
    # This grant does not permit rescheduling or owner operations.
    with pytest.raises(PermissionDenied):
        await AdminService(demo.sessions).employees(demo.employee_user)


async def test_owner_filters_are_backend_protected(demo):
    service = BookingService(demo.sessions, lambda: NOW)
    booking = await service.book(demo.customer, command(demo))
    for user in (demo.customer, demo.employee_user):
        with pytest.raises(PermissionDenied):
            await service.list(user, scope="admin", filter_employee_id=demo.employee)
    assert [
        item.id
        for item in await service.list(
            demo.admin,
            scope="admin",
            filter_employee_id=demo.employee,
            service_id=demo.service,
            status="confirmed",
        )
    ] == [booking.id]
    assert await service.list(demo.admin, scope="admin", status="cancelled") == []


async def test_owner_configuration_is_trusted_and_revocable(demo):
    from appointment_booking.app.services.user_service import UserService

    user = await UserService(demo.sessions, [100]).identify(100, "Клієнт", "en")
    assert user.primary_role == "OWNER" and user.user.locale == "uk"
    revoked = await UserService(demo.sessions, []).identify(100, "Клієнт", "en")
    assert revoked.primary_role == "CUSTOMER" and not revoked.admin
    with pytest.raises(PermissionDenied):
        await AdminService(demo.sessions).employees(demo.customer)


async def test_inactive_linked_account_requires_reactivation_not_duplicate_profile(demo):
    service = AdminService(demo.sessions, lambda: NOW)
    await service.save_employee(
        demo.admin, EmployeeInput(name="Барбер", active=False), demo.employee, 1
    )
    token = await service.invite(demo.admin, demo.employee2)
    from appointment_booking.app.core.exceptions import PolicyViolation

    with pytest.raises(PolicyViolation):
        await service.activate_invitation(demo.employee_user, token)
    reactivated = await service.save_employee(
        demo.admin, EmployeeInput(name="Барбер", active=True), demo.employee, 2
    )
    assert reactivated.user_id == demo.employee_user
    async with demo.sessions() as session:
        assert (await actor_for(session, demo.employee_user)).primary_role == "BARBER"


async def test_time_off_requests_are_scoped_and_owner_approval_checks_appointments(demo):
    from appointment_booking.app.core.exceptions import StaleSelection
    from appointment_booking.app.domain.dto import BlockInput

    service = AdminService(demo.sessions, lambda: NOW)
    await BookingService(demo.sessions, lambda: NOW).book(demo.customer, command(demo))
    conflict = BlockInput(starts_at=START, ends_at=START + timedelta(hours=2), reason="Вихідний")
    with pytest.raises(PermissionDenied):
        await service.request_time_off(demo.customer, conflict)
    await service.request_time_off(demo.employee_user, conflict)
    requests = await service.time_off_requests(demo.employee_user)
    item = requests[0][0]
    assert (await service.time_off_requests(demo.admin))[0][0].id == item.id
    with pytest.raises(PermissionDenied):
        await service.review_time_off(demo.employee_user, item.id, True)
    with pytest.raises(ScheduleConflict):
        await service.review_time_off(demo.admin, item.id, True)
    assert (await service.time_off_requests(demo.admin))[0][0].status == "pending"
    await service.review_time_off(demo.admin, item.id, False)
    with pytest.raises(StaleSelection):
        await service.review_time_off(demo.admin, item.id, True)
    assert not await service.time_off_requests(demo.admin)
    assert (await service.time_off_requests(demo.employee_user))[0][0].status == "rejected"
    free = BlockInput(
        starts_at=START + timedelta(days=1),
        ends_at=START + timedelta(days=1, hours=2),
        reason="Особисті справи",
    )
    await service.request_time_off(demo.employee_user, free)
    item = (await service.time_off_requests(demo.admin))[0][0]
    await service.review_time_off(demo.admin, item.id, True)
    blocks = await service.special(demo.employee_user, "blocks", 0, demo.employee)
    assert len(blocks) == 1 and blocks[0].starts_at == free.starts_at


async def test_independent_employee_availability_permissions(demo):
    from appointment_booking.app.core.exceptions import PolicyViolation
    from appointment_booking.app.domain.dto import BlockInput

    service = AdminService(demo.sessions, lambda: NOW)
    data = BlockInput(starts_at=START, ends_at=START + timedelta(hours=1), reason="Перерва")
    await service.save_employee(
        demo.admin,
        EmployeeInput(
            name="Барбер",
            may_manage_availability=True,
            may_block_periods=False,
            may_request_time_off=False,
        ),
        demo.employee,
        1,
    )
    with pytest.raises(PermissionDenied):
        await service.block(
            demo.employee_user, data.model_copy(update={"employee_id": demo.employee})
        )
    with pytest.raises(PermissionDenied):
        await service.request_time_off(demo.employee_user, data)
    await service.save_employee(
        demo.admin, EmployeeInput(name="Барбер", may_request_time_off=True), demo.employee, 2
    )
    with pytest.raises(PolicyViolation):
        await service.request_time_off(
            demo.employee_user, data.model_copy(update={"employee_id": demo.employee2})
        )
    await service.request_time_off(demo.employee_user, data)
    with pytest.raises(PermissionDenied):
        await service.time_off_requests(demo.customer)
