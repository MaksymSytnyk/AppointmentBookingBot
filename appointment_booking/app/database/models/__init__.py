"""Persistence schema. Invariants are duplicated in migrations, never created at startup."""

from datetime import date, datetime, time
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Entity:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(Entity, Base):
    __tablename__ = "users"
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    locale: Mapped[str] = mapped_column(String(2), default="uk")
    phone: Mapped[str | None] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(default=True)
    __table_args__ = (CheckConstraint("locale IN ('en', 'uk')", name="locale"),)


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), primary_key=True)
    __table_args__ = (CheckConstraint("role IN ('CUSTOMER', 'BARBER', 'OWNER')", name="role"),)


class Employee(Entity, Base):
    __tablename__ = "employees"
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    bio: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(default=True)
    is_demo: Mapped[bool] = mapped_column(default=False)
    may_manage_availability: Mapped[bool] = mapped_column(default=False)
    may_cancel_appointments: Mapped[bool] = mapped_column(default=False)
    may_block_periods: Mapped[bool] = mapped_column(default=False)
    may_request_time_off: Mapped[bool] = mapped_column(default=False)
    revision: Mapped[int] = mapped_column(default=1)


class EmployeeInvitation(Entity, Base):
    __tablename__ = "employee_invitations"
    employee_id: Mapped[UUID] = mapped_column(ForeignKey("employees.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    target_telegram_id: Mapped[int | None] = mapped_column(BigInteger)


class TimeOffRequest(Entity, Base):
    __tablename__ = "time_off_requests"
    employee_id: Mapped[UUID] = mapped_column(ForeignKey("employees.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="interval"),
        CheckConstraint("status IN ('pending','approved','rejected')", name="status"),
    )


class Service(Entity, Base):
    __tablename__ = "services"
    name: Mapped[dict[str, str]] = mapped_column(JSONB)
    description: Mapped[dict[str, str]] = mapped_column(JSONB)
    duration_minutes: Mapped[int]
    buffer_before: Mapped[int] = mapped_column(default=0)
    buffer_after: Mapped[int] = mapped_column(default=0)
    price_minor: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3), default="UAH")
    active: Mapped[bool] = mapped_column(default=True)
    revision: Mapped[int] = mapped_column(default=1)
    __table_args__ = (
        CheckConstraint("duration_minutes BETWEEN 1 AND 1440", name="duration"),
        CheckConstraint(
            "buffer_before BETWEEN 0 AND 1440 AND buffer_after BETWEEN 0 AND 1440", name="buffers"
        ),
        CheckConstraint("price_minor >= 0", name="price"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency"),
    )


class EmployeeService(Base):
    __tablename__ = "employee_services"
    employee_id: Mapped[UUID] = mapped_column(ForeignKey("employees.id"), primary_key=True)
    service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id"), primary_key=True)


class BusinessSettings(Entity, Base):
    __tablename__ = "business_settings"
    singleton: Mapped[bool] = mapped_column(Boolean, default=True, unique=True)
    name: Mapped[str] = mapped_column(String(100), default="ATELIER BARBER")
    about: Mapped[str] = mapped_column(
        Text, default="Точна стрижка, догляд за бородою та увага до деталей. 💈"
    )
    contacts: Mapped[str] = mapped_column(
        Text, default="Контакти ще не додано. Звернися до адміністратора барбершопу."
    )
    timezone: Mapped[str] = mapped_column(String(80), default="Europe/Kyiv")
    slot_minutes: Mapped[int] = mapped_column(default=15)
    lead_minutes: Mapped[int] = mapped_column(default=60)
    horizon_days: Mapped[int] = mapped_column(default=30)
    cancellation_hours: Mapped[int] = mapped_column(default=12)
    notify_admins: Mapped[bool] = mapped_column(default=True)
    reminders_enabled: Mapped[bool] = mapped_column(default=True)
    reminder_hours: Mapped[list[int]] = mapped_column(JSONB, default=lambda: [24, 2])
    revision: Mapped[int] = mapped_column(default=1)
    __table_args__ = (
        CheckConstraint("singleton", name="singleton"),
        CheckConstraint("slot_minutes BETWEEN 1 AND 120", name="slots"),
        CheckConstraint("lead_minutes BETWEEN 0 AND 43200", name="lead"),
        CheckConstraint("horizon_days BETWEEN 1 AND 365", name="horizon"),
        CheckConstraint("cancellation_hours BETWEEN 0 AND 720", name="cancellation"),
    )


class WorkingHours(Entity, Base):
    __tablename__ = "working_hours"
    weekday: Mapped[int]
    opens: Mapped[time] = mapped_column(Time)
    closes: Mapped[time] = mapped_column(Time)
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday"),
        CheckConstraint("closes > opens", name="interval"),
        UniqueConstraint("weekday", "opens"),
    )


class EmployeeWorkingHours(Entity, Base):
    """A closed row explicitly overrides inherited business hours on a weekday."""

    __tablename__ = "employee_working_hours"
    employee_id: Mapped[UUID] = mapped_column(ForeignKey("employees.id"), index=True)
    weekday: Mapped[int]
    closed: Mapped[bool] = mapped_column(default=False)
    opens: Mapped[time | None] = mapped_column(Time)
    closes: Mapped[time | None] = mapped_column(Time)
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday"),
        CheckConstraint(
            "(closed AND opens IS NULL AND closes IS NULL) OR "
            "(NOT closed AND opens IS NOT NULL AND closes IS NOT NULL AND closes > opens)",
            name="interval",
        ),
    )


class Holiday(Entity, Base):
    __tablename__ = "holidays"
    day: Mapped[date] = mapped_column(Date, unique=True)
    label: Mapped[str] = mapped_column(String(100))


class ScheduleException(Entity, Base):
    __tablename__ = "schedule_exceptions"
    employee_id: Mapped[UUID | None] = mapped_column(ForeignKey("employees.id"), index=True)
    day: Mapped[date] = mapped_column(Date)
    closed: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (
        Index(
            "uq_exception_business_day",
            "day",
            unique=True,
            postgresql_where=text("employee_id IS NULL"),
        ),
        UniqueConstraint("employee_id", "day"),
    )


class ExceptionInterval(Entity, Base):
    __tablename__ = "exception_intervals"
    exception_id: Mapped[UUID] = mapped_column(
        ForeignKey("schedule_exceptions.id", ondelete="CASCADE"), index=True
    )
    # UTC endpoints explicitly disambiguate DST boundaries.
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("ends_at > starts_at", name="interval"),)


class BlockedPeriod(Entity, Base):
    __tablename__ = "blocked_periods"
    employee_id: Mapped[UUID | None] = mapped_column(ForeignKey("employees.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(300))
    creator_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="interval"),
        Index(
            "ix_blocked_range", func.tstzrange(starts_at, ends_at, "[)"), postgresql_using="gist"
        ),
    )


class Appointment(Entity, Base):
    __tablename__ = "appointments"
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    employee_id: Mapped[UUID] = mapped_column(ForeignKey("employees.id"))
    service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id"))
    reference: Mapped[str] = mapped_column(String(16), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="confirmed")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    occupied_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    occupied_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_minutes: Mapped[int]
    buffer_before: Mapped[int]
    buffer_after: Mapped[int]
    price_minor: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3))
    service_label: Mapped[dict[str, str]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(default=1)
    cancelled_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    cancellation_reason: Mapped[str | None] = mapped_column(String(300))
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','confirmed','cancelled','completed','no_show','expired')",
            name="status",
        ),
        CheckConstraint(
            "duration_minutes > 0 AND buffer_before >= 0 AND buffer_after >= 0 "
            "AND price_minor >= 0 AND version > 0",
            name="snapshots",
        ),
        CheckConstraint(
            "ends_at = starts_at + duration_minutes * interval '1 minute' "
            "AND occupied_start = starts_at - buffer_before * interval '1 minute' "
            "AND occupied_end = ends_at + buffer_after * interval '1 minute'",
            name="occupancy",
        ),
        ExcludeConstraint(
            ("employee_id", "="),
            (func.tstzrange(occupied_start, occupied_end, "[)"), "&&"),
            where=text("status IN ('pending','confirmed','completed','no_show')"),
            name="appointments_no_overlap",
            using="gist",
        ),
        Index("ix_appointment_employee_start", "employee_id", "starts_at"),
        Index("ix_appointment_customer_start", "customer_id", "starts_at"),
        Index("ix_appointment_status_start", "status", "starts_at"),
    )


class AppointmentEvent(Entity, Base):
    __tablename__ = "appointment_events"
    appointment_id: Mapped[UUID] = mapped_column(ForeignKey("appointments.id"), index=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40))
    version: Mapped[int]
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class BookingRequest(Entity, Base):
    __tablename__ = "booking_requests"
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    token: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    appointment_id: Mapped[UUID] = mapped_column(ForeignKey("appointments.id"))
    __table_args__ = (UniqueConstraint("customer_id", "token"),)


class NotificationJob(Entity, Base):
    __tablename__ = "notification_jobs"
    appointment_id: Mapped[UUID] = mapped_column(ForeignKey("appointments.id"), index=True)
    appointment_version: Mapped[int]
    recipient_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(40))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(16), default="queued")
    attempts: Mapped[int] = mapped_column(default=0)
    lease_token: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("appointment_id", "appointment_version", "recipient_id", "kind"),
        CheckConstraint("state IN ('queued','sending','sent','dead','superseded')", name="state"),
        CheckConstraint("attempts >= 0", name="attempts"),
        Index("ix_jobs_due", "next_attempt_at", postgresql_where=text("state = 'queued'")),
        Index("ix_jobs_leases", "lease_until", postgresql_where=text("state = 'sending'")),
    )


class NotificationLog(Entity, Base):
    __tablename__ = "notification_logs"
    job_id: Mapped[UUID] = mapped_column(ForeignKey("notification_jobs.id"), index=True)
    attempt: Mapped[int]
    outcome: Mapped[str] = mapped_column(String(40))
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (UniqueConstraint("job_id", "attempt"),)


class AdminAudit(Entity, Base):
    __tablename__ = "admin_audit"
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[UUID | None]
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
