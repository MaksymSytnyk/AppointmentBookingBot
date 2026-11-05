import asyncio
import secrets
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from appointment_booking.app.core.exceptions import (
    BusinessError,
    PermissionDenied,
    PolicyViolation,
    SlotUnavailable,
    StaleSelection,
)
from appointment_booking.app.database.models import (
    Appointment,
    AppointmentEvent,
    NotificationJob,
)
from appointment_booking.app.domain.dto import BlockInput, BookCommand, HoursInput
from appointment_booking.app.services.admin_service import AdminService
from appointment_booking.app.services.booking_service import BookingService
from tests.integration.conftest import NOW, START

pytestmark = pytest.mark.integration


def command(demo, **changes):
    values = dict(
        service_id=demo.service,
        employee_id=demo.employee,
        starts_at=START,
        request_token=secrets.token_urlsafe(16),
        service_revision=1,
        settings_revision=1,
    )
    values.update(changes)
    return BookCommand(**values)


async def count(demo, model):
    async with demo.sessions() as session:
        return await session.scalar(select(func.count()).select_from(model))


async def test_create_cancel_and_release(demo):
    service = BookingService(demo.sessions, lambda: NOW)
    item = await service.book(demo.customer, command(demo))
    assert await count(demo, Appointment) == 1
    assert await count(demo, AppointmentEvent) == 2
    assert await count(demo, NotificationJob) >= 3
    await service.change(demo.customer, item.id, version=1, action="cancelled")
    replacement = await service.book(demo.other, command(demo))
    assert replacement.id != item.id


async def test_idempotent_confirmation_and_mismatched_replay(demo):
    service = BookingService(demo.sessions, lambda: NOW)
    request = command(demo)
    first = await service.book(demo.customer, request)
    second = await service.book(demo.customer, request)
    assert first.id == second.id
    assert await count(demo, Appointment) == 1
    assert (await service.replay(demo.customer, request.request_token)).id == first.id
    with pytest.raises(StaleSelection):
        await service.book(
            demo.customer, request.model_copy(update={"starts_at": START + timedelta(hours=2)})
        )


async def test_permissions_enforced_without_telegram(demo):
    service = BookingService(demo.sessions, lambda: NOW)
    item = await service.book(demo.customer, command(demo))
    for action in ("cancelled", "completed", "no_show"):
        with pytest.raises(PermissionDenied):
            await service.change(demo.other, item.id, version=1, action=action)
    with pytest.raises(PermissionDenied):
        await service.detail(demo.other, item.id)
    assert (await service.detail(demo.employee_user, item.id))[0].id == item.id
    with pytest.raises(PermissionDenied):
        await AdminService(demo.sessions, lambda: NOW).employees(demo.customer)


async def test_failed_reschedule_preserves_original_and_jobs(demo):
    service = BookingService(demo.sessions, lambda: NOW)
    item = await service.book(demo.customer, command(demo))
    await service.book(demo.other, command(demo, starts_at=START + timedelta(hours=2)))
    jobs_before, events_before = (
        await count(demo, NotificationJob),
        await count(demo, AppointmentEvent),
    )
    with pytest.raises(SlotUnavailable):
        await service.change(
            demo.customer,
            item.id,
            version=1,
            action="rescheduled",
            starts_at=START + timedelta(hours=2),
        )
    current, _, _ = await service.detail(demo.customer, item.id)
    assert current.starts_at == START and current.version == 1
    assert await count(demo, NotificationJob) == jobs_before
    assert await count(demo, AppointmentEvent) == events_before
    moved = await service.change(
        demo.customer,
        item.id,
        version=1,
        action="rescheduled",
        starts_at=START + timedelta(hours=4),
        employee_id=demo.employee2,
    )
    assert moved.version == 2 and moved.employee_id == demo.employee2


async def test_cutoff_and_terminal_rules(demo):
    service = BookingService(demo.sessions, lambda: NOW)
    item = await service.book(demo.customer, command(demo))
    near = BookingService(demo.sessions, lambda: START - timedelta(hours=1))
    with pytest.raises(PolicyViolation):
        await near.change(demo.customer, item.id, version=1, action="cancelled")
    with pytest.raises(PolicyViolation):
        await near.change(demo.employee_user, item.id, version=1, action="completed")
    end = BookingService(demo.sessions, lambda: START + timedelta(hours=1))
    completed = await end.change(demo.employee_user, item.id, version=1, action="completed")
    assert completed.status == "completed"


async def race(*operations):
    barrier = asyncio.Barrier(len(operations))

    async def ready(operation):
        await barrier.wait()
        try:
            return await operation
        except BusinessError as exc:
            return exc

    return await asyncio.wait_for(asyncio.gather(*(ready(op) for op in operations)), 15)


@pytest.mark.parametrize("minutes", [0, 30, 60])
async def test_concurrent_overlapping_bookings_have_one_winner(demo, minutes):
    service = BookingService(demo.sessions, lambda: NOW)
    results = await race(
        service.book(demo.customer, command(demo)),
        service.book(demo.other, command(demo, starts_at=START + timedelta(minutes=minutes))),
    )
    assert sum(isinstance(r, Appointment) for r in results) == 1
    assert sum(isinstance(r, SlotUnavailable) for r in results) == 1
    assert await count(demo, Appointment) == 1


async def test_concurrent_distinct_employees_and_adjacent_ranges(demo):
    service = BookingService(demo.sessions, lambda: NOW)
    results = await race(
        service.book(demo.customer, command(demo)),
        service.book(demo.other, command(demo, employee_id=demo.employee2)),
    )
    assert all(isinstance(r, Appointment) for r in results)
    adjacent = await service.book(
        demo.other, command(demo, starts_at=START + timedelta(minutes=75))
    )
    assert adjacent.starts_at == START + timedelta(minutes=75)


async def test_database_constraint_cannot_be_bypassed(demo):
    service = BookingService(demo.sessions, lambda: NOW)
    original = await service.book(demo.customer, command(demo))

    async def insert_direct():
        async with demo.sessions.begin() as session:
            item = Appointment(
                customer_id=demo.other,
                employee_id=demo.employee,
                service_id=demo.service,
                reference=secrets.token_hex(6),
                status="confirmed",
                starts_at=original.starts_at,
                ends_at=original.ends_at,
                occupied_start=original.occupied_start,
                occupied_end=original.occupied_end,
                duration_minutes=60,
                buffer_before=0,
                buffer_after=15,
                price_minor=100,
                currency="UAH",
                service_label={"en": "X", "uk": "X"},
            )
            session.add(item)

    with pytest.raises(IntegrityError) as error:
        await insert_direct()
    assert "appointments_no_overlap" in str(error.value.orig)
    assert await count(demo, Appointment) == 1


async def test_schedule_edit_and_booking_race(demo):
    bookings, admin = (
        BookingService(demo.sessions, lambda: NOW),
        AdminService(demo.sessions, lambda: NOW),
    )
    block = BlockInput(
        employee_id=demo.employee,
        starts_at=START,
        ends_at=START + timedelta(hours=1),
        reason="Leave",
    )
    results = await race(
        bookings.book(demo.customer, command(demo)), admin.block(demo.admin, block)
    )
    assert sum(isinstance(r, BusinessError) for r in results) == 1
    # Either the block or booking wins, never an invalid confirmed booking under a block.


async def test_cannot_close_hours_over_existing_booking(demo):
    service = BookingService(demo.sessions, lambda: NOW)
    await service.book(demo.customer, command(demo))
    with pytest.raises(PolicyViolation):
        await AdminService(demo.sessions, lambda: NOW).hours(
            demo.admin, HoursInput(employee_id=demo.employee, weekday=START.weekday(), intervals=[])
        )


async def test_completion_does_not_release_unelapsed_buffer(demo):
    initial = BookingService(demo.sessions, lambda: NOW)
    item = await initial.book(demo.customer, command(demo))
    at_end = BookingService(demo.sessions, lambda: START + timedelta(hours=1))
    await at_end.change(demo.employee_user, item.id, version=1, action="completed")
    with pytest.raises(SlotUnavailable):
        await at_end.book(demo.other, command(demo, starts_at=START + timedelta(hours=1)))
    allowed = await at_end.book(demo.other, command(demo, starts_at=START + timedelta(minutes=75)))
    assert allowed.starts_at == START + timedelta(minutes=75)


async def test_concurrent_replayed_token_creates_one_appointment(demo):
    service = BookingService(demo.sessions, lambda: NOW)
    request = command(demo)
    results = await race(service.book(demo.customer, request), service.book(demo.customer, request))
    assert all(isinstance(r, Appointment) for r in results)
    assert results[0].id == results[1].id
    assert await count(demo, Appointment) == 1


async def test_reschedule_competes_atomically_with_another_booking(demo):
    service = BookingService(demo.sessions, lambda: NOW)
    item = await service.book(demo.customer, command(demo))
    new_start = START + timedelta(hours=2)
    results = await race(
        service.change(
            demo.customer, item.id, version=1, action="rescheduled", starts_at=new_start
        ),
        service.book(demo.other, command(demo, starts_at=new_start)),
    )
    assert sum(isinstance(r, SlotUnavailable) for r in results) == 1
    current, _, _ = await service.detail(demo.customer, item.id)
    if isinstance(results[0], SlotUnavailable):
        assert current.starts_at == START and current.version == 1
    else:
        assert current.starts_at == new_start and current.version == 2
