from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from appointment_booking.app.core.clock import Clock, utcnow
from appointment_booking.app.core.exceptions import (
    PolicyViolation,
    ScheduleConflict,
    StaleSelection,
)
from appointment_booking.app.database.models import (
    BlockedPeriod,
    Employee,
    ExceptionInterval,
    Holiday,
    ScheduleException,
    Service,
)
from appointment_booking.app.database.repositories import (
    AppointmentRepository,
    CatalogRepository,
    ManagementRepository,
    NotificationRepository,
    ScheduleRepository,
    UserRepository,
    audit,
)
from appointment_booking.app.domain.availability import Interval
from appointment_booking.app.domain.dto import (
    BlockInput,
    EmployeeInput,
    ExceptionInput,
    HoursInput,
    ServiceInput,
    SettingsInput,
)
from appointment_booking.app.services.access import actor_for
from appointment_booking.app.services.availability_service import working_intervals


class AdminService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], clock: Clock = utcnow) -> None:
        self.sessions, self.clock = sessions, clock

    async def _guard_schedule(self, session: AsyncSession, employee_id: UUID | None = None) -> None:
        await session.flush()
        settings = await CatalogRepository(session).settings()
        zone = ZoneInfo(settings.timezone)
        repo = ScheduleRepository(session)
        affected = []
        for appointment in await AppointmentRepository(session).future(self.clock(), employee_id):
            day = appointment.starts_at.astimezone(zone).date()
            span = Interval(appointment.occupied_start, appointment.occupied_end)
            working = await working_intervals(session, settings, appointment.employee_id, day)
            blocks = await repo.blocks(span.start, span.end, appointment.employee_id)
            if not any(w.contains(span) for w in working) or any(
                span.overlaps(Interval(b.starts_at, b.ends_at)) for b in blocks
            ):
                affected.append(appointment.reference)
        if affected:
            raise ScheduleConflict(affected)

    async def employees(self, user_id: UUID, page: int = 0) -> list[Employee]:
        async with self.sessions() as session:
            (await actor_for(session, user_id)).require_admin()
            return await CatalogRepository(session).employees(page=max(0, page), active=False)

    async def services(self, user_id: UUID, page: int = 0) -> list[Service]:
        async with self.sessions() as session:
            (await actor_for(session, user_id)).require_admin()
            return await CatalogRepository(session).services(page=max(0, page), active=False)

    async def save_service(
        self,
        user_id: UUID,
        data: ServiceInput,
        identifier: UUID | None = None,
        revision: int | None = None,
    ) -> Service:
        async with self.sessions.begin() as session:
            (await actor_for(session, user_id)).require_admin()
            repo = CatalogRepository(session)
            await repo.settings(write=True)
            if identifier:
                item = await repo.service(identifier)
                if item.revision != revision:
                    raise StaleSelection()
                item.revision += 1
            else:
                item = Service()
                session.add(item)
            for key, value in data.model_dump().items():
                setattr(item, key, value)
            await session.flush()
            audit(session, user_id, "service_saved", item.id)
            return item

    async def save_employee(
        self,
        user_id: UUID,
        data: EmployeeInput,
        identifier: UUID | None = None,
        revision: int | None = None,
    ) -> Employee:
        async with self.sessions.begin() as session:
            (await actor_for(session, user_id)).require_admin()
            repo = CatalogRepository(session)
            await repo.settings(write=True)
            if identifier:
                item = await repo.employee(identifier, lock=True)
                if item.revision != revision:
                    raise StaleSelection()
                future = await AppointmentRepository(session).future(self.clock(), item.id)
                if not data.active and future:
                    raise ScheduleConflict([a.reference for a in future])
                item.revision += 1
            else:
                item = Employee()
                session.add(item)
            old_user = item.user_id
            if data.user_id and data.user_id != old_user:
                raise PolicyViolation("Use an invitation to link an account")
            for key, value in data.model_dump(exclude={"user_id"}).items():
                setattr(item, key, value)
            await session.flush()
            await ManagementRepository(session).linked_role(
                old_user, item.user_id if item.active else None
            )
            audit(session, user_id, "employee_saved", item.id)
            return item

    async def assignments(self, user_id: UUID, employee_id: UUID) -> list[UUID]:
        async with self.sessions() as session:
            (await actor_for(session, user_id)).require_admin()
            return await CatalogRepository(session).assignments(employee_id)

    async def assign(
        self, user_id: UUID, employee_id: UUID, service_id: UUID, enabled: bool
    ) -> None:
        async with self.sessions.begin() as session:
            (await actor_for(session, user_id)).require_admin()
            repo = CatalogRepository(session)
            await repo.settings(write=True)
            await repo.employee(employee_id, lock=True)
            await repo.service(service_id)
            affected = [
                a.reference
                for a in await AppointmentRepository(session).future(self.clock(), employee_id)
                if a.service_id == service_id
            ]
            if not enabled and affected:
                raise ScheduleConflict(affected)
            await repo.assign(employee_id, service_id, enabled)
            audit(
                session, user_id, "assignment_changed", employee_id, {"service_id": str(service_id)}
            )

    async def configure(self, user_id: UUID, data: SettingsInput, revision: int) -> None:
        async with self.sessions.begin() as session:
            (await actor_for(session, user_id)).require_admin()
            item = await CatalogRepository(session).settings(write=True)
            if item.revision != revision:
                raise StaleSelection()
            future = await AppointmentRepository(session).future(self.clock())
            if item.timezone != data.timezone and future:
                raise ScheduleConflict([a.reference for a in future])
            for key, value in data.model_dump().items():
                setattr(item, key, value)
            item.revision += 1
            audit(session, user_id, "settings_changed", item.id)

    async def hours(self, user_id: UUID, data: HoursInput) -> None:
        async with self.sessions.begin() as session:
            actor = await actor_for(session, user_id)
            actor.manage_availability(data.employee_id)
            await CatalogRepository(session).settings(write=True)
            (await actor_for(session, user_id)).manage_availability(data.employee_id)
            if data.employee_id:
                await CatalogRepository(session).employee(data.employee_id, lock=True)
            await ScheduleRepository(session).replace_hours(
                data.employee_id, data.weekday, data.intervals, data.inherit
            )
            await self._guard_schedule(session, data.employee_id)
            audit(session, user_id, "hours_changed", data.employee_id, {"weekday": data.weekday})

    async def read_hours(self, user_id: UUID, employee_id: UUID | None, weekday: int) -> list[Any]:
        if not 0 <= weekday <= 6:
            raise PolicyViolation()
        async with self.sessions() as session:
            (await actor_for(session, user_id)).manage_availability(employee_id)
            return await ScheduleRepository(session).recurring(weekday, employee_id)

    async def exception(self, user_id: UUID, data: ExceptionInput) -> None:
        async with self.sessions.begin() as session:
            (await actor_for(session, user_id)).manage_availability(data.employee_id)
            settings = await CatalogRepository(session).settings(write=True)
            (await actor_for(session, user_id)).manage_availability(data.employee_id)
            zone = ZoneInfo(settings.timezone)
            local_end = datetime.combine(data.day + timedelta(days=1), time(), zone).astimezone(UTC)
            if any(
                start.astimezone(zone).date() != data.day or end > local_end
                for start, end in data.intervals
            ):
                raise PolicyViolation()
            repo = ScheduleRepository(session)
            previous, _ = await repo.exception(data.day, data.employee_id)
            if previous:
                await session.delete(previous)
                await session.flush()
            item = ScheduleException(
                employee_id=data.employee_id, day=data.day, closed=not data.intervals
            )
            session.add(item)
            await session.flush()
            session.add_all(
                ExceptionInterval(exception_id=item.id, starts_at=a, ends_at=b)
                for a, b in data.intervals
            )
            await self._guard_schedule(session, data.employee_id)
            audit(session, user_id, "exception_saved", item.id)

    async def block(self, user_id: UUID, data: BlockInput) -> None:
        async with self.sessions.begin() as session:
            (await actor_for(session, user_id)).manage_blocks(data.employee_id)
            await CatalogRepository(session).settings(write=True)
            (await actor_for(session, user_id)).manage_blocks(data.employee_id)
            item = BlockedPeriod(**data.model_dump(), creator_id=user_id)
            session.add(item)
            await self._guard_schedule(session, data.employee_id)
            audit(session, user_id, "block_created", item.id)

    async def holiday(self, user_id: UUID, day: date, label: str) -> None:
        if not 1 <= len(label) <= 100:
            raise PolicyViolation()
        async with self.sessions.begin() as session:
            (await actor_for(session, user_id)).require_admin()
            await CatalogRepository(session).settings(write=True)
            item = Holiday(day=day, label=label)
            session.add(item)
            await self._guard_schedule(session)
            audit(session, user_id, "holiday_created", item.id)

    async def special(
        self, user_id: UUID, kind: str, page: int, employee_id: UUID | None = None
    ) -> list[Any]:
        if kind not in {"holidays", "blocks", "exceptions"}:
            raise PolicyViolation()
        async with self.sessions() as session:
            actor = await actor_for(session, user_id)
            if kind == "holidays":
                actor.require_admin()
            elif kind == "blocks":
                actor.manage_blocks(employee_id)
            else:
                actor.manage_availability(employee_id)
            return await ScheduleRepository(session).list_special(kind, max(0, page), employee_id)

    async def remove_special(self, user_id: UUID, kind: str, identifier: UUID) -> None:
        model = {"blocks": BlockedPeriod, "holidays": Holiday, "exceptions": ScheduleException}.get(
            kind
        )
        if model is None:
            raise PolicyViolation()
        async with self.sessions.begin() as session:
            actor = await actor_for(session, user_id)
            await CatalogRepository(session).settings(write=True)
            actor = await actor_for(session, user_id)
            item = await ManagementRepository(session).special(kind, identifier)
            if item is None:
                raise StaleSelection()
            if kind == "holidays":
                actor.require_admin()
            elif kind == "blocks":
                actor.manage_blocks(item.employee_id)
            else:
                actor.manage_availability(item.employee_id)
            await session.delete(item)
            await self._guard_schedule(session)
            audit(session, user_id, "schedule_entry_removed", identifier)

    async def customers(self, user_id: UUID, term: str, page: int = 0) -> list[Any]:
        async with self.sessions() as session:
            (await actor_for(session, user_id)).require_admin()
            return await UserRepository(session).search(term, max(0, page))

    async def statistics(self, user_id: UUID, first: date, last: date) -> dict[str, Any]:
        if last < first or (last - first).days > 366:
            raise PolicyViolation()
        async with self.sessions() as session:
            (await actor_for(session, user_id)).require_admin()
            settings = await CatalogRepository(session).settings()
            zone = ZoneInfo(settings.timezone)
            start = datetime.combine(first, time(), zone).astimezone(UTC)
            end = datetime.combine(last + timedelta(days=1), time(), zone).astimezone(UTC)
            return await AppointmentRepository(session).statistics(start, end, self.clock())

    async def jobs(self, user_id: UUID, page: int = 0) -> tuple[dict[str, int], list[Any]]:
        async with self.sessions() as session:
            (await actor_for(session, user_id)).require_admin()
            repo = NotificationRepository(session)
            return await repo.counts(), await repo.pending(max(0, page))

    async def retry_job(self, user_id: UUID, identifier: UUID) -> None:
        async with self.sessions.begin() as session:
            (await actor_for(session, user_id)).require_admin()
            item = await NotificationRepository(session).job(identifier)
            if item.state != "dead":
                raise StaleSelection()
            item.state, item.next_attempt_at = "queued", self.clock()
            audit(session, user_id, "notification_retry", identifier)

    async def invite(
        self, user_id: UUID, employee_id: UUID, target_telegram_id: int | None = None
    ) -> str:
        import hashlib
        import secrets

        from sqlalchemy import update

        from appointment_booking.app.database.models import EmployeeInvitation

        if target_telegram_id is not None and target_telegram_id <= 0:
            raise PolicyViolation()
        token = secrets.token_urlsafe(32)
        async with self.sessions.begin() as session:
            (await actor_for(session, user_id)).require_admin()
            await CatalogRepository(session).settings(write=True)
            employee = await CatalogRepository(session).employee(employee_id, lock=True)
            if not employee.active or employee.user_id or employee.is_demo:
                raise PolicyViolation()
            # Reissuing an invitation revokes previous links for this employee.
            await session.execute(
                update(EmployeeInvitation)
                .where(
                    EmployeeInvitation.employee_id == employee_id,
                    EmployeeInvitation.used_at.is_(None),
                )
                .values(expires_at=self.clock())
            )
            session.add(
                EmployeeInvitation(
                    employee_id=employee_id,
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    expires_at=self.clock() + timedelta(hours=24),
                    target_telegram_id=target_telegram_id,
                )
            )
            audit(session, user_id, "employee_invited", employee_id)
        return token

    async def activate_invitation(self, user_id: UUID, token: str) -> Employee:
        import hashlib

        from sqlalchemy import select

        from appointment_booking.app.core.exceptions import PermissionDenied
        from appointment_booking.app.database.models import EmployeeInvitation

        async with self.sessions.begin() as session:
            actor = await actor_for(session, user_id)
            catalog = CatalogRepository(session)
            await catalog.settings(write=True)
            await UserRepository(session).lock(actor.user)
            invitation = await session.scalar(
                select(EmployeeInvitation)
                .where(EmployeeInvitation.token_hash == hashlib.sha256(token.encode()).hexdigest())
                .with_for_update()
            )
            if (
                invitation is None
                or invitation.used_at is not None
                or invitation.expires_at <= self.clock()
                or (
                    invitation.target_telegram_id is not None
                    and invitation.target_telegram_id != actor.user.telegram_id
                )
            ):
                raise PermissionDenied()
            employee = await catalog.employee(invitation.employee_id, lock=True)
            linked_employee = await session.scalar(
                select(Employee.id).where(Employee.user_id == user_id)
            )
            if linked_employee or employee.user_id or not employee.active or employee.is_demo:
                raise PolicyViolation()
            employee.user_id = user_id
            employee.revision += 1
            invitation.used_at = self.clock()
            await ManagementRepository(session).linked_role(None, user_id)
            audit(session, user_id, "employee_invitation_activated", employee.id)
            return employee

    async def barber_profile(self, user_id: UUID) -> tuple[Any, list[Any], list[Any]]:
        async with self.sessions() as session:
            actor = await actor_for(session, user_id)
            actor.require_barber()
            assert actor.employee is not None
            catalog = CatalogRepository(session)
            services = [
                await catalog.service(i) for i in await catalog.assignments(actor.employee.id)
            ]
            hours = [
                await ScheduleRepository(session).recurring(day, actor.employee.id)
                or await ScheduleRepository(session).recurring(day)
                for day in range(7)
            ]
            return actor.employee, services, hours

    async def create_employee(
        self,
        user_id: UUID,
        data: EmployeeInput,
        service_ids: list[UUID],
        weekdays: list[int],
        intervals: list[tuple[time, time]],
    ) -> Employee:
        from appointment_booking.app.database.models import EmployeeService
        from appointment_booking.app.domain.availability import validate_hours

        validate_hours(intervals)
        if (
            data.user_id
            or not service_ids
            or not weekdays
            or not intervals
            or any(day not in range(7) for day in weekdays)
        ):
            raise PolicyViolation()
        async with self.sessions.begin() as session:
            (await actor_for(session, user_id)).require_admin()
            catalog = CatalogRepository(session)
            await catalog.settings(write=True)
            for identifier in set(service_ids):
                if not (await catalog.service(identifier)).active:
                    raise PolicyViolation()
            employee = Employee(**data.model_dump())
            session.add(employee)
            await session.flush()
            session.add_all(
                EmployeeService(employee_id=employee.id, service_id=identifier)
                for identifier in set(service_ids)
            )
            for weekday in range(7):
                await ScheduleRepository(session).replace_hours(
                    employee.id, weekday, intervals if weekday in weekdays else [], False
                )
            audit(
                session,
                user_id,
                "employee_created",
                employee.id,
                {"services": [str(i) for i in service_ids], "weekdays": weekdays},
            )
            return employee

    async def request_time_off(self, user_id: UUID, data: BlockInput) -> None:
        from appointment_booking.app.core.exceptions import PermissionDenied
        from appointment_booking.app.database.models import TimeOffRequest

        async with self.sessions.begin() as session:
            await CatalogRepository(session).settings(write=True)
            actor = await actor_for(session, user_id)
            actor.require_barber()
            assert actor.employee is not None
            if not actor.employee.may_request_time_off:
                raise PermissionDenied()
            if data.employee_id not in {None, actor.employee.id} or data.starts_at <= self.clock():
                raise PolicyViolation()
            item = TimeOffRequest(
                employee_id=actor.employee.id,
                starts_at=data.starts_at,
                ends_at=data.ends_at,
                reason=data.reason,
                status="pending",
            )
            session.add(item)
            await session.flush()
            audit(session, user_id, "time_off_requested", item.id)

    async def time_off_requests(self, user_id: UUID, page: int = 0) -> list[Any]:
        from sqlalchemy import select

        from appointment_booking.app.database.models import TimeOffRequest

        async with self.sessions() as session:
            actor = await actor_for(session, user_id)
            query = select(TimeOffRequest, Employee.name).join(Employee)
            if actor.admin:
                query = query.where(TimeOffRequest.status == "pending")
            else:
                actor.require_barber()
                assert actor.employee is not None
                query = query.where(TimeOffRequest.employee_id == actor.employee.id)
            return list(
                (
                    await session.execute(
                        query.order_by(TimeOffRequest.created_at.desc(), TimeOffRequest.id)
                        .offset(max(0, page) * 8)
                        .limit(8)
                    )
                ).all()
            )

    async def review_time_off(self, user_id: UUID, identifier: UUID, approve: bool) -> None:
        from sqlalchemy import select

        from appointment_booking.app.database.models import TimeOffRequest

        async with self.sessions.begin() as session:
            (await actor_for(session, user_id)).require_admin()
            await CatalogRepository(session).settings(write=True)
            item = await session.scalar(
                select(TimeOffRequest).where(TimeOffRequest.id == identifier).with_for_update()
            )
            if item is None or item.status != "pending":
                raise StaleSelection()
            employee = await CatalogRepository(session).employee(item.employee_id, lock=True)
            if approve:
                if not employee.active or item.starts_at <= self.clock():
                    raise PolicyViolation()
                session.add(
                    BlockedPeriod(
                        employee_id=item.employee_id,
                        starts_at=item.starts_at,
                        ends_at=item.ends_at,
                        reason=item.reason,
                        creator_id=user_id,
                    )
                )
                await self._guard_schedule(session, item.employee_id)
            item.status = "approved" if approve else "rejected"
            audit(session, user_id, "time_off_" + item.status, item.id)
