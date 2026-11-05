from datetime import timedelta

import pytest

from appointment_booking.app.core.exceptions import (
    PermissionDenied,
    ScheduleConflict,
    StaleSelection,
)
from appointment_booking.app.domain.dto import (
    EmployeeInput,
    ExceptionInput,
    HoursInput,
    ProfileInput,
    ServiceInput,
)
from appointment_booking.app.services.admin_service import AdminService
from appointment_booking.app.services.availability_service import AvailabilityService
from appointment_booking.app.services.booking_service import BookingService
from appointment_booking.app.services.user_service import UserService
from tests.integration.conftest import NOW, START
from tests.integration.test_bookings import command

pytestmark = pytest.mark.integration


async def test_holiday_exception_and_employee_closure_precedence(demo):
    admin = AdminService(demo.sessions, lambda: NOW)
    availability = AvailabilityService(demo.sessions, lambda: NOW)
    day = START.date()
    assert await availability.slots(demo.customer, demo.service, day, demo.employee)
    await admin.holiday(demo.admin, day, "Closed")
    assert not await availability.slots(demo.customer, demo.service, day, demo.employee)
    await admin.exception(
        demo.admin,
        ExceptionInput(day=day, intervals=[(START.replace(hour=9), START.replace(hour=18))]),
    )
    assert await availability.slots(demo.customer, demo.service, day, demo.employee)
    await admin.exception(
        demo.admin, ExceptionInput(employee_id=demo.employee, day=day, intervals=[])
    )
    assert not await availability.slots(demo.customer, demo.service, day, demo.employee)
    assert await availability.slots(demo.customer, demo.service, day, demo.employee2)


async def test_split_hours_closed_override_and_inheritance(demo):
    admin = AdminService(demo.sessions, lambda: NOW)
    availability = AvailabilityService(demo.sessions, lambda: NOW)
    from datetime import time

    await admin.hours(
        demo.admin,
        HoursInput(weekday=START.weekday(), intervals=[(time(9), time(12)), (time(13), time(18))]),
    )
    slots = await availability.slots(demo.customer, demo.service, START.date(), demo.employee)
    assert START.replace(hour=11) not in {s.starts_at for s in slots}
    await admin.hours(
        demo.employee_user,
        HoursInput(employee_id=demo.employee, weekday=START.weekday(), intervals=[]),
    )
    assert not await availability.slots(demo.customer, demo.service, START.date(), demo.employee)
    await admin.hours(
        demo.employee_user,
        HoursInput(employee_id=demo.employee, weekday=START.weekday(), intervals=[], inherit=True),
    )
    assert await availability.slots(demo.customer, demo.service, START.date(), demo.employee)
    with pytest.raises(PermissionDenied):
        await admin.hours(
            demo.employee_user,
            HoursInput(employee_id=demo.employee2, weekday=START.weekday(), intervals=[]),
        )


async def test_catalog_edits_preserve_price_and_duration_snapshots(demo):
    bookings, admin = (
        BookingService(demo.sessions, lambda: NOW),
        AdminService(demo.sessions, lambda: NOW),
    )
    original = await bookings.book(demo.customer, command(demo))
    data = ServiceInput(
        name={"en": "New cut", "uk": "Нова стрижка"},
        description={"en": "Updated", "uk": "Оновлено"},
        duration_minutes=30,
        price_minor=100000,
        buffer_after=5,
    )
    await admin.save_service(demo.admin, data, demo.service, revision=1)
    with pytest.raises(StaleSelection):
        await bookings.book(demo.other, command(demo, starts_at=START + timedelta(hours=4)))
    moved = await bookings.change(
        demo.customer,
        original.id,
        version=1,
        action="rescheduled",
        starts_at=START + timedelta(hours=2),
    )
    assert moved.duration_minutes == 60 and moved.buffer_after == 15 and moved.price_minor == 80000
    with pytest.raises(StaleSelection):
        await admin.save_service(demo.admin, data, demo.service, revision=1)


async def test_employee_deactivation_requires_resolving_bookings(demo):
    await BookingService(demo.sessions, lambda: NOW).book(demo.customer, command(demo))
    with pytest.raises(ScheduleConflict) as error:
        await AdminService(demo.sessions, lambda: NOW).save_employee(
            demo.admin,
            EmployeeInput(name="Unavailable", user_id=demo.employee_user, active=False),
            demo.employee,
            revision=1,
        )
    assert len(error.value.references) == 1


async def test_profile_and_customer_search_authorization(demo):
    users = UserService(demo.sessions, [])
    await users.profile(
        demo.customer, ProfileInput(display_name="Updated", locale="uk", phone="+380501234567")
    )
    admin = AdminService(demo.sessions, lambda: NOW)
    results = await admin.customers(demo.admin, "Updated")
    assert len(results) == 1 and results[0].locale == "uk"
    with pytest.raises(PermissionDenied):
        await admin.customers(demo.employee_user, "Updated")


async def test_analytics_and_history_after_lifecycle(demo):
    bookings = BookingService(demo.sessions, lambda: NOW)
    item = await bookings.book(demo.customer, command(demo))
    await bookings.change(demo.customer, item.id, version=1, action="cancelled")
    stats = await AdminService(demo.sessions, lambda: NOW).statistics(
        demo.admin, START.date(), START.date()
    )
    assert stats["total"] == 1 and stats["counts"]["cancelled"] == 1 and stats["upcoming"] == 0
    history = await bookings.list(demo.customer, history=True)
    assert len(history) == 1 and history[0].id == item.id
    assert not await bookings.list(demo.customer)
