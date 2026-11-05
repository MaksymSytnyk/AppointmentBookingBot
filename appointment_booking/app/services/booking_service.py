import asyncio
import hashlib
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from appointment_booking.app.core.clock import Clock, utcnow
from appointment_booking.app.core.exceptions import (
    PermissionDenied,
    PolicyViolation,
    SlotUnavailable,
    StaleSelection,
)
from appointment_booking.app.database.models import Appointment, AppointmentEvent, BookingRequest
from appointment_booking.app.database.repositories import (
    AppointmentRepository,
    CatalogRepository,
    UserRepository,
)
from appointment_booking.app.domain.dto import BookCommand
from appointment_booking.app.domain.policies import check_cutoff, transition
from appointment_booking.app.services.access import actor_for
from appointment_booking.app.services.availability_service import starts_for
from appointment_booking.app.services.notification_service import enqueue_notifications

T = TypeVar("T")


def snapshot(appointment: Appointment) -> dict[str, str | int]:
    return {
        "employee_id": str(appointment.employee_id),
        "starts_at": appointment.starts_at.isoformat(),
        "ends_at": appointment.ends_at.isoformat(),
        "status": appointment.status,
        "version": appointment.version,
    }


def fingerprint(command: BookCommand) -> str:
    return hashlib.sha256(command.model_dump_json().encode()).hexdigest()


class RetryLockOrder(Exception):
    pass


class BookingService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], clock: Clock = utcnow) -> None:
        self.sessions, self.clock = sessions, clock

    async def _transaction(self, operation: Callable[[AsyncSession], Awaitable[T]]) -> T:
        for attempt in range(3):
            try:
                async with self.sessions.begin() as session:
                    return await operation(session)
            except IntegrityError as exc:
                # Only the named range constraint is a customer-facing slot conflict.
                if getattr(
                    exc.orig, "sqlstate", ""
                ) == "23P01" and "appointments_no_overlap" in str(exc.orig):
                    raise SlotUnavailable() from exc
                raise
            except (DBAPIError, RetryLockOrder) as exc:
                state = getattr(getattr(exc, "orig", None), "sqlstate", "")
                if not isinstance(exc, RetryLockOrder) and state not in {"40001", "40P01", "55P03"}:
                    raise
                if attempt == 2:
                    raise PolicyViolation("Busy; retry the operation") from exc
                await asyncio.sleep(0.03 * 2**attempt + secrets.randbelow(30) / 1000)
        raise RuntimeError("Unreachable")

    async def book(self, user_id: UUID, command: BookCommand) -> Appointment:
        async def operation(session: AsyncSession) -> Appointment:
            actor = await actor_for(session, user_id)
            catalog, bookings = CatalogRepository(session), AppointmentRepository(session)
            settings = await catalog.settings(lock=True)
            # Serialize request tokens per customer, including requests for different employees.
            await UserRepository(session).lock(actor.user)
            existing = await bookings.request(user_id, command.request_token)
            if existing:
                if existing.fingerprint != fingerprint(command):
                    raise StaleSelection()
                return await bookings.get(existing.appointment_id)
            employee = await catalog.employee(command.employee_id, lock=True)
            service = await catalog.service(command.service_id)
            if (
                not employee.active
                or not service.active
                or not await catalog.assigned(employee.id, service.id)
            ):
                raise SlotUnavailable()
            if (
                service.revision != command.service_revision
                or settings.revision != command.settings_revision
            ):
                raise StaleSelection()
            start = command.starts_at.astimezone(UTC)
            day = start.astimezone(ZoneInfo(settings.timezone)).date()
            if start not in await starts_for(
                session, settings, employee.id, service, day, self.clock()
            ):
                raise SlotUnavailable()
            end = start + timedelta(minutes=service.duration_minutes)
            appointment = Appointment(
                customer_id=user_id,
                employee_id=employee.id,
                service_id=service.id,
                reference=secrets.token_hex(6).upper(),
                status="confirmed",
                starts_at=start,
                ends_at=end,
                occupied_start=start - timedelta(minutes=service.buffer_before),
                occupied_end=end + timedelta(minutes=service.buffer_after),
                duration_minutes=service.duration_minutes,
                buffer_before=service.buffer_before,
                buffer_after=service.buffer_after,
                price_minor=service.price_minor,
                currency=service.currency,
                service_label=dict(service.name),
                version=1,
            )
            session.add(appointment)
            await session.flush()
            for action in ("created", "confirmed"):
                session.add(
                    AppointmentEvent(
                        appointment_id=appointment.id,
                        actor_id=user_id,
                        action=action,
                        version=1,
                        details=snapshot(appointment),
                    )
                )
            session.add(
                BookingRequest(
                    customer_id=user_id,
                    token=command.request_token,
                    fingerprint=fingerprint(command),
                    appointment_id=appointment.id,
                )
            )
            await enqueue_notifications(session, appointment, settings, "confirmed", self.clock())
            return appointment

        return await self._transaction(operation)

    async def change(
        self,
        user_id: UUID,
        appointment_id: UUID,
        *,
        version: int,
        action: str,
        reason: str = "",
        starts_at: datetime | None = None,
        employee_id: UUID | None = None,
    ) -> Appointment:
        if action not in {"cancelled", "rescheduled", "completed", "no_show"}:
            raise PolicyViolation()
        if len(reason) > 300:
            raise PolicyViolation()

        async def operation(session: AsyncSession) -> Appointment:
            actor = await actor_for(session, user_id)
            catalog, bookings = CatalogRepository(session), AppointmentRepository(session)
            settings = await catalog.settings(lock=True)
            previous = await bookings.get(appointment_id)
            actor.read(previous)
            old_employee = previous.employee_id
            target_employee = employee_id or old_employee
            for identifier in sorted({old_employee, target_employee}, key=str):
                await catalog.employee(identifier, lock=True)
            appointment = await bookings.get(appointment_id, lock=True)
            actor = await actor_for(session, user_id)
            if appointment.employee_id != old_employee:
                raise RetryLockOrder()
            if appointment.version != version:
                raise StaleSelection()
            before = snapshot(appointment)
            now = self.clock()
            if action in {"cancelled", "rescheduled"}:
                barber_cancel = (
                    action == "cancelled"
                    and actor.employee is not None
                    and actor.employee.id == appointment.employee_id
                    and actor.employee.may_cancel_appointments
                )
                if not actor.admin and appointment.customer_id != user_id and not barber_cancel:
                    raise PermissionDenied()
                check_cutoff(
                    appointment.starts_at, now, settings.cancellation_hours, admin=actor.admin
                )
                if actor.admin and appointment.customer_id != user_id and not reason.strip():
                    raise PolicyViolation("Administrator changes require a reason")
                if (
                    actor.admin
                    and appointment.starts_at - now < timedelta(hours=settings.cancellation_hours)
                    and not reason.strip()
                ):
                    raise PolicyViolation("Cutoff override requires a reason")
            elif not actor.admin and (
                actor.employee is None or actor.employee.id != appointment.employee_id
            ):
                raise PermissionDenied()
            if action == "rescheduled":
                if appointment.status != "confirmed" or starts_at is None:
                    raise PolicyViolation()
                from appointment_booking.app.domain.availability import require_aware

                require_aware(starts_at)
                target = await catalog.employee(target_employee)
                service = await catalog.service(appointment.service_id)
                if (
                    not target.active
                    or not service.active
                    or not await catalog.assigned(target.id, service.id)
                ):
                    raise SlotUnavailable()
                start = starts_at.astimezone(UTC)
                day = start.astimezone(ZoneInfo(settings.timezone)).date()
                if start not in await starts_for(
                    session, settings, target.id, appointment, day, now, appointment.id
                ):
                    raise SlotUnavailable()
                appointment.employee_id = target.id
                appointment.starts_at = start
                appointment.ends_at = start + timedelta(minutes=appointment.duration_minutes)
                appointment.occupied_start = start - timedelta(minutes=appointment.buffer_before)
                appointment.occupied_end = appointment.ends_at + timedelta(
                    minutes=appointment.buffer_after
                )
            else:
                transition(appointment.status, action, ends_at=appointment.ends_at, now=now)
                appointment.status = action
                if action == "cancelled":
                    appointment.cancelled_by = user_id
                    appointment.cancellation_reason = reason
            appointment.version += 1
            await session.flush()
            session.add(
                AppointmentEvent(
                    appointment_id=appointment.id,
                    actor_id=user_id,
                    action=action,
                    version=appointment.version,
                    details={"before": before, "after": snapshot(appointment), "reason": reason},
                )
            )
            await enqueue_notifications(session, appointment, settings, action, now, old_employee)
            return appointment

        return await self._transaction(operation)

    async def detail(self, user_id: UUID, identifier: UUID) -> tuple[Appointment, str, str]:
        async with self.sessions() as session:
            actor = await actor_for(session, user_id)
            item = await AppointmentRepository(session).get(identifier)
            actor.read(item)
            employee = await CatalogRepository(session).employee(item.employee_id)
            customer = await UserRepository(session).get(item.customer_id)
            return item, employee.name, customer.display_name

    async def events(
        self, user_id: UUID, identifier: UUID, page: int = 0
    ) -> list[AppointmentEvent]:
        async with self.sessions() as session:
            actor = await actor_for(session, user_id)
            item = await AppointmentRepository(session).get(identifier)
            actor.read(item)
            return await AppointmentRepository(session).events(item.id, max(0, page))

    async def list(
        self,
        user_id: UUID,
        *,
        scope: str = "mine",
        filter_employee_id: UUID | None = None,
        service_id: UUID | None = None,
        status: str | None = None,
        page: int = 0,
        history: bool = False,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Appointment]:
        async with self.sessions() as session:
            actor = await actor_for(session, user_id)
            customer_id, employee_id = None, None
            if filter_employee_id or service_id or status:
                actor.require_admin()
            if status and status not in {
                "confirmed",
                "cancelled",
                "completed",
                "no_show",
                "pending",
                "expired",
            }:
                raise PolicyViolation()
            if scope == "mine":
                customer_id = user_id
            elif scope == "employee" and actor.employee:
                employee_id = actor.employee.id
            elif scope == "admin":
                actor.require_admin()
                employee_id = filter_employee_id
            else:
                raise PermissionDenied()
            return await AppointmentRepository(session).list(
                customer_id=customer_id,
                employee_id=employee_id,
                page=max(0, page),
                service_id=service_id,
                status=status,
                history=history,
                start=start,
                end=end,
                now=self.clock(),
            )

    async def replay(self, user_id: UUID, token: str) -> Appointment:
        async with self.sessions() as session:
            await actor_for(session, user_id)
            request = await AppointmentRepository(session).request(user_id, token)
            if request is None:
                raise StaleSelection()
            return await AppointmentRepository(session).get(request.appointment_id)
