"""Explicit query boundaries. Repositories never commit transactions."""

from __future__ import annotations

import builtins
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from appointment_booking.app.core.exceptions import NotFound
from appointment_booking.app.database.models import (
    AdminAudit,
    Appointment,
    AppointmentEvent,
    BlockedPeriod,
    BookingRequest,
    BusinessSettings,
    Employee,
    EmployeeService,
    EmployeeWorkingHours,
    ExceptionInterval,
    Holiday,
    NotificationJob,
    NotificationLog,
    ScheduleException,
    Service,
    User,
    UserRole,
    WorkingHours,
)


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def identify(self, telegram_id: int, name: str, locale: str, admin: bool) -> User:
        query = insert(User).values(telegram_id=telegram_id, display_name=name[:100], locale=locale)
        await self.session.execute(query.on_conflict_do_nothing(index_elements=[User.telegram_id]))
        user = (
            await self.session.scalars(select(User).where(User.telegram_id == telegram_id))
        ).one()
        user.locale = "uk"
        await self.session.execute(
            insert(UserRole).values(user_id=user.id, role="CUSTOMER").on_conflict_do_nothing()
        )
        if not admin:
            await self.session.execute(
                delete(UserRole).where(UserRole.user_id == user.id, UserRole.role == "OWNER")
            )
        if admin:
            granted = await self.session.scalar(
                insert(UserRole)
                .values(user_id=user.id, role="OWNER")
                .on_conflict_do_nothing()
                .returning(UserRole.user_id)
            )
            if granted:
                audit(self.session, user.id, "admin_bootstrapped", user.id)
        return user

    async def get(self, user_id: UUID) -> User:
        value = await self.session.get(User, user_id, populate_existing=True)
        if value is None or not value.active:
            raise NotFound()
        return value

    async def lock(self, user: User) -> None:
        await self.session.refresh(user, with_for_update=True)

    async def recipient(self, user_id: UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def roles(self, user_id: UUID) -> set[str]:
        return set(
            await self.session.scalars(select(UserRole.role).where(UserRole.user_id == user_id))
        )

    async def employee(self, user_id: UUID) -> Employee | None:
        return (
            await self.session.scalars(
                select(Employee)
                .where(Employee.user_id == user_id, Employee.active.is_(True))
                .execution_options(populate_existing=True)
            )
        ).first()

    async def search(self, term: str, page: int) -> list[User]:
        statement = select(User).order_by(User.created_at.desc(), User.id)
        if term.isdigit():
            statement = statement.where(User.telegram_id == int(term))
        else:
            statement = statement.where(User.display_name.ilike(f"%{term[:100]}%"))
        return list(await self.session.scalars(statement.offset(page * 8).limit(8)))

    async def administrators(self) -> list[UUID]:
        return list(
            await self.session.scalars(select(UserRole.user_id).where(UserRole.role == "OWNER"))
        )


class CatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def settings(self, *, write: bool = False, lock: bool = False) -> BusinessSettings:
        statement = select(BusinessSettings)
        if lock or write:
            statement = statement.with_for_update(read=not write)
        item = await self.session.scalar(statement)
        if item is None:
            raise NotFound()
        return item

    async def services(self, *, active: bool = True, page: int = 0) -> list[Service]:
        statement = select(Service).order_by(Service.created_at, Service.id)
        if active:
            statement = statement.where(Service.active.is_(True))
        return list(await self.session.scalars(statement.offset(page * 8).limit(8)))

    async def service(self, identifier: UUID) -> Service:
        item = await self.session.get(Service, identifier)
        if item is None:
            raise NotFound()
        return item

    async def employees(
        self, service_id: UUID | None = None, page: int = 0, *, active: bool = True
    ) -> list[Employee]:
        statement = select(Employee).order_by(Employee.name, Employee.id)
        if active:
            statement = statement.where(Employee.active.is_(True))
        else:
            # Retired fictional seed profiles are kept for history, not employee management.
            statement = statement.where(or_(Employee.active.is_(True), Employee.is_demo.is_(False)))
        if service_id:
            statement = statement.join(EmployeeService).where(
                EmployeeService.service_id == service_id
            )
        return list(await self.session.scalars(statement.offset(page * 8).limit(8)))

    async def qualified(self, service_id: UUID) -> list[Employee]:
        return list(
            await self.session.scalars(
                select(Employee)
                .join(EmployeeService)
                .where(EmployeeService.service_id == service_id, Employee.active.is_(True))
                .order_by(Employee.id)
            )
        )

    async def employee(self, identifier: UUID, *, lock: bool = False) -> Employee:
        statement = select(Employee).where(Employee.id == identifier)
        if lock:
            statement = statement.with_for_update()
        item = await self.session.scalar(statement.execution_options(populate_existing=True))
        if item is None:
            raise NotFound()
        return item

    async def assigned(self, employee_id: UUID, service_id: UUID) -> bool:
        return await self.session.get(EmployeeService, (employee_id, service_id)) is not None

    async def assignments(self, employee_id: UUID) -> list[UUID]:
        return list(
            await self.session.scalars(
                select(EmployeeService.service_id).where(EmployeeService.employee_id == employee_id)
            )
        )

    async def assign(self, employee_id: UUID, service_id: UUID, enabled: bool) -> None:
        if enabled:
            await self.session.execute(
                insert(EmployeeService)
                .values(employee_id=employee_id, service_id=service_id)
                .on_conflict_do_nothing()
            )
        else:
            await self.session.execute(
                delete(EmployeeService).where(
                    EmployeeService.employee_id == employee_id,
                    EmployeeService.service_id == service_id,
                )
            )


class ScheduleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def recurring(
        self, weekday: int, employee_id: UUID | None = None
    ) -> list[WorkingHours] | list[EmployeeWorkingHours]:
        if employee_id is None:
            return list(
                await self.session.scalars(
                    select(WorkingHours)
                    .where(WorkingHours.weekday == weekday)
                    .order_by(WorkingHours.opens)
                )
            )
        return list(
            await self.session.scalars(
                select(EmployeeWorkingHours)
                .where(
                    EmployeeWorkingHours.employee_id == employee_id,
                    EmployeeWorkingHours.weekday == weekday,
                )
                .order_by(EmployeeWorkingHours.opens)
            )
        )

    async def exception(
        self, day: Any, employee_id: UUID | None
    ) -> tuple[ScheduleException | None, list[ExceptionInterval]]:
        statement = select(ScheduleException).where(ScheduleException.day == day)
        statement = statement.where(ScheduleException.employee_id == employee_id)
        exception = await self.session.scalar(statement)
        intervals = (
            []
            if exception is None
            else list(
                await self.session.scalars(
                    select(ExceptionInterval).where(ExceptionInterval.exception_id == exception.id)
                )
            )
        )
        return exception, intervals

    async def holiday(self, day: Any) -> bool:
        return await self.session.scalar(select(Holiday.id).where(Holiday.day == day)) is not None

    async def blocks(
        self, start: datetime, end: datetime, employee_id: UUID
    ) -> list[BlockedPeriod]:
        return list(
            await self.session.scalars(
                select(BlockedPeriod).where(
                    or_(
                        BlockedPeriod.employee_id == employee_id,
                        BlockedPeriod.employee_id.is_(None),
                    ),
                    BlockedPeriod.starts_at < end,
                    BlockedPeriod.ends_at > start,
                )
            )
        )

    async def occupied(
        self, start: datetime, end: datetime, employee_id: UUID, exclude: UUID | None = None
    ) -> builtins.list[Appointment]:
        statement = select(Appointment).where(
            Appointment.employee_id == employee_id,
            Appointment.status.in_(["pending", "confirmed", "completed", "no_show"]),
            Appointment.occupied_start < end,
            Appointment.occupied_end > start,
        )
        if exclude:
            statement = statement.where(Appointment.id != exclude)
        return list(await self.session.scalars(statement))

    async def replace_hours(
        self, employee_id: UUID | None, weekday: int, intervals: list[Any], inherit: bool
    ) -> None:
        if employee_id is None:
            await self.session.execute(delete(WorkingHours).where(WorkingHours.weekday == weekday))
            self.session.add_all(
                WorkingHours(weekday=weekday, opens=a, closes=b) for a, b in intervals
            )
        else:
            await self.session.execute(
                delete(EmployeeWorkingHours).where(
                    EmployeeWorkingHours.employee_id == employee_id,
                    EmployeeWorkingHours.weekday == weekday,
                )
            )
            if not intervals and not inherit:
                self.session.add(
                    EmployeeWorkingHours(employee_id=employee_id, weekday=weekday, closed=True)
                )
            self.session.add_all(
                EmployeeWorkingHours(
                    employee_id=employee_id, weekday=weekday, opens=a, closes=b, closed=False
                )
                for a, b in intervals
            )

    async def list_special(
        self, kind: str, page: int, employee_id: UUID | None = None
    ) -> list[Any]:
        model: Any = {
            "blocks": BlockedPeriod,
            "holidays": Holiday,
            "exceptions": ScheduleException,
        }[kind]
        statement = select(model).order_by(model.created_at.desc(), model.id)
        if employee_id is not None:
            if kind == "blocks":
                statement = statement.where(BlockedPeriod.employee_id == employee_id)
            elif kind == "exceptions":
                statement = statement.where(ScheduleException.employee_id == employee_id)
        return list(await self.session.scalars(statement.offset(page * 8).limit(8)))


class AppointmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, identifier: UUID, *, lock: bool = False) -> Appointment:
        statement = select(Appointment).where(Appointment.id == identifier)
        if lock:
            statement = statement.with_for_update()
        item = await self.session.scalar(statement.execution_options(populate_existing=True))
        if item is None:
            raise NotFound()
        return item

    async def request(self, customer_id: UUID, token: str) -> BookingRequest | None:
        return (
            await self.session.scalars(
                select(BookingRequest).where(
                    BookingRequest.customer_id == customer_id, BookingRequest.token == token
                )
            )
        ).first()

    async def list(
        self,
        *,
        customer_id: UUID | None = None,
        employee_id: UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        service_id: UUID | None = None,
        status: str | None = None,
        history: bool = False,
        page: int = 0,
        now: datetime | None = None,
    ) -> builtins.list[Appointment]:
        statement = select(Appointment)
        if customer_id:
            statement = statement.where(Appointment.customer_id == customer_id)
        if employee_id:
            statement = statement.where(Appointment.employee_id == employee_id)
        if service_id:
            statement = statement.where(Appointment.service_id == service_id)
        if status:
            statement = statement.where(Appointment.status == status)
        if start:
            statement = statement.where(Appointment.starts_at >= start)
        if end:
            statement = statement.where(Appointment.starts_at < end)
        if customer_id:
            assert now is not None
            if history:
                statement = statement.where(
                    or_(Appointment.status != "confirmed", Appointment.ends_at <= now)
                )
            else:
                statement = statement.where(
                    Appointment.status == "confirmed", Appointment.ends_at > now
                )
        return list(
            await self.session.scalars(
                statement.order_by(
                    Appointment.starts_at.desc() if history else Appointment.starts_at,
                    Appointment.id,
                )
                .offset(page * 8)
                .limit(8)
            )
        )

    async def future(
        self, now: datetime, employee_id: UUID | None = None
    ) -> builtins.list[Appointment]:
        query = select(Appointment).where(
            Appointment.status == "confirmed", Appointment.ends_at > now
        )
        if employee_id:
            query = query.where(Appointment.employee_id == employee_id)
        return list(await self.session.scalars(query))

    async def events(self, appointment_id: UUID, page: int) -> builtins.list[AppointmentEvent]:
        return list(
            await self.session.scalars(
                select(AppointmentEvent)
                .where(AppointmentEvent.appointment_id == appointment_id)
                .order_by(AppointmentEvent.created_at.desc())
                .offset(page * 8)
                .limit(8)
            )
        )

    async def statistics(self, start: datetime, end: datetime, now: datetime) -> dict[str, Any]:
        bounds = (Appointment.starts_at >= start, Appointment.starts_at < end)
        counts: dict[str, int] = dict(
            (
                await self.session.execute(
                    select(Appointment.status, func.count())
                    .where(*bounds)
                    .group_by(Appointment.status)
                )
            )
            .tuples()
            .all()
        )
        popular = (
            await self.session.execute(
                select(Service.name, func.count())
                .join(Appointment, Appointment.service_id == Service.id)
                .where(*bounds)
                .group_by(Service.id)
                .order_by(func.count().desc())
                .limit(5)
            )
        ).all()
        workload = (
            await self.session.execute(
                select(Employee.name, func.count(), func.sum(Appointment.duration_minutes))
                .join(Appointment, Appointment.employee_id == Employee.id)
                .where(*bounds, Appointment.status != "cancelled")
                .group_by(Employee.id)
            )
        ).all()
        upcoming = await self.session.scalar(
            select(func.count())
            .select_from(Appointment)
            .where(*bounds, Appointment.status == "confirmed", Appointment.starts_at >= now)
        )
        return {
            "counts": counts,
            "total": sum(counts.values()),
            "upcoming": upcoming,
            "popular": [(n, c) for n, c in popular],
            "workload": [tuple(w) for w in workload],
        }


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def supersede(self, appointment_id: UUID) -> None:
        await self.session.execute(
            update(NotificationJob)
            .where(
                NotificationJob.appointment_id == appointment_id,
                NotificationJob.state.in_(["queued", "sending"]),
            )
            .values(state="superseded", lease_token=None, lease_until=None)
        )

    async def claim(self, now: datetime) -> NotificationJob | None:
        return (
            await self.session.scalars(
                select(NotificationJob)
                .where(
                    or_(
                        (NotificationJob.state == "queued")
                        & (NotificationJob.next_attempt_at <= now),
                        (NotificationJob.state == "sending") & (NotificationJob.lease_until <= now),
                    )
                )
                .order_by(NotificationJob.next_attempt_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
        ).first()

    async def fenced(self, job_id: UUID, token: UUID) -> NotificationJob | None:
        return (
            await self.session.scalars(
                select(NotificationJob)
                .where(
                    NotificationJob.id == job_id,
                    NotificationJob.lease_token == token,
                    NotificationJob.state == "sending",
                )
                .with_for_update()
            )
        ).first()

    async def pending(self, page: int) -> list[NotificationJob]:
        return list(
            await self.session.scalars(
                select(NotificationJob)
                .where(NotificationJob.state == "dead")
                .order_by(NotificationJob.created_at)
                .offset(page * 8)
                .limit(8)
            )
        )

    async def job(self, identifier: UUID) -> NotificationJob:
        job = await self.session.get(NotificationJob, identifier, with_for_update=True)
        if job is None:
            raise NotFound()
        return job

    async def counts(self) -> dict[str, int]:
        rows = await self.session.execute(
            select(NotificationJob.state, func.count()).group_by(NotificationJob.state)
        )
        return {state: count for state, count in rows}

    async def attempt(self, job_id: UUID, attempt: int) -> NotificationLog | None:
        return (
            await self.session.scalars(
                select(NotificationLog).where(
                    NotificationLog.job_id == job_id, NotificationLog.attempt == attempt
                )
            )
        ).first()


def audit(
    session: AsyncSession,
    actor: UUID,
    action: str,
    entity: UUID | None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(AdminAudit(actor_id=actor, action=action, entity_id=entity, details=details or {}))


class ManagementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def linked_role(self, old: UUID | None, new: UUID | None) -> None:
        if old and old != new:
            await self.session.execute(
                delete(UserRole).where(UserRole.user_id == old, UserRole.role == "BARBER")
            )
        if new:
            await self.session.execute(
                insert(UserRole).values(user_id=new, role="BARBER").on_conflict_do_nothing()
            )

    async def special(self, kind: str, identifier: UUID) -> Any:
        model: Any = {
            "blocks": BlockedPeriod,
            "holidays": Holiday,
            "exceptions": ScheduleException,
        }[kind]
        return await self.session.get(model, identifier, with_for_update=True)
