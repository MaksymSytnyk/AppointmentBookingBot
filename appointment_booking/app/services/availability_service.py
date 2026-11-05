from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from appointment_booking.app.core.clock import Clock, utcnow
from appointment_booking.app.core.exceptions import PolicyViolation
from appointment_booking.app.database.models import Appointment, BusinessSettings, Service
from appointment_booking.app.database.repositories import CatalogRepository, ScheduleRepository
from appointment_booking.app.domain.availability import (
    Interval,
    available_starts,
    intersect,
    wall_interval,
)
from appointment_booking.app.services.access import actor_for


@dataclass(frozen=True)
class Slot:
    employee_id: UUID
    employee_name: str
    starts_at: datetime


async def working_intervals(
    session: AsyncSession, settings: BusinessSettings, employee_id: UUID, day: date
) -> list[Interval]:
    repo = ScheduleRepository(session)
    zone = ZoneInfo(settings.timezone)

    async def resolve(scope: UUID | None, fallback: list[Interval]) -> list[Interval]:
        exception, intervals = await repo.exception(day, scope)
        if exception:
            return [] if exception.closed else [Interval(i.starts_at, i.ends_at) for i in intervals]
        if scope is None and await repo.holiday(day):
            return []
        rows = await repo.recurring(day.weekday(), scope)
        if not rows:
            return fallback
        if any(getattr(row, "closed", False) for row in rows):
            return []
        try:
            return [
                wall_interval(day, row.opens, row.closes, zone)
                for row in rows
                if row.opens is not None and row.closes is not None
            ]
        except ValueError as exc:
            raise PolicyViolation("Dated exception required for DST boundary") from exc

    business = await resolve(None, [])
    employee = await resolve(employee_id, business)
    return intersect(business, employee)


async def starts_for(
    session: AsyncSession,
    settings: BusinessSettings,
    employee_id: UUID,
    service: Service | Appointment,
    day: date,
    now: datetime,
    exclude: UUID | None = None,
) -> list[datetime]:
    zone = ZoneInfo(settings.timezone)
    start = datetime.combine(day, time(), zone).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time(), zone).astimezone(UTC)
    repo = ScheduleRepository(session)
    busy = [
        Interval(a.occupied_start, a.occupied_end)
        for a in await repo.occupied(start, end, employee_id, exclude)
    ]
    busy.extend(
        Interval(b.starts_at, b.ends_at) for b in await repo.blocks(start, end, employee_id)
    )
    return available_starts(
        day=day,
        zone=zone,
        working=await working_intervals(session, settings, employee_id, day),
        occupied=busy,
        duration=service.duration_minutes,
        before=service.buffer_before,
        after=service.buffer_after,
        step=settings.slot_minutes,
        now=now,
        lead_minutes=settings.lead_minutes,
        horizon_days=settings.horizon_days,
    )


class AvailabilityService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], clock: Clock = utcnow) -> None:
        self.sessions, self.clock = sessions, clock

    async def slots(
        self,
        user_id: UUID,
        service_id: UUID,
        day: date,
        employee_id: UUID | None = None,
        appointment_id: UUID | None = None,
    ) -> list[Slot]:
        async with self.sessions() as session:
            actor = await actor_for(session, user_id)
            catalog = CatalogRepository(session)
            settings = await catalog.settings()
            catalog_service = await catalog.service(service_id)
            if not catalog_service.active:
                return []
            service: Service | Appointment = catalog_service
            if appointment_id:
                from appointment_booking.app.database.repositories import AppointmentRepository

                appointment = await AppointmentRepository(session).get(appointment_id)
                actor.read(appointment)
                if not actor.admin and appointment.customer_id != user_id:
                    from appointment_booking.app.core.exceptions import PermissionDenied

                    raise PermissionDenied()
                service = appointment
            employees = await catalog.qualified(service_id)
            results = []
            for employee in employees:
                if employee_id and employee_id != employee.id:
                    continue
                for start in await starts_for(
                    session, settings, employee.id, service, day, self.clock(), appointment_id
                ):
                    results.append(Slot(employee.id, employee.name, start))
            return sorted(results, key=lambda s: (s.starts_at, str(s.employee_id)))
