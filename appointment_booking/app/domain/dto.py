from datetime import UTC, date, datetime, time
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from appointment_booking.app.domain.availability import require_aware, validate_hours


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BookCommand(Command):
    service_id: UUID
    employee_id: UUID
    starts_at: datetime
    request_token: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    service_revision: int = Field(ge=1)
    settings_revision: int = Field(ge=1)

    @field_validator("starts_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        require_aware(value)
        return value


class ServiceInput(Command):
    name: dict[str, str]
    description: dict[str, str]
    duration_minutes: int = Field(ge=1, le=1440)
    buffer_before: int = Field(default=0, ge=0, le=1440)
    buffer_after: int = Field(default=0, ge=0, le=1440)
    price_minor: int = Field(ge=0, le=100_000_000)
    currency: str = Field(default="UAH", pattern=r"^[A-Z]{3}$")
    active: bool = True

    @field_validator("name", "description")
    @classmethod
    def translations(cls, value: dict[str, str], info: ValidationInfo) -> dict[str, str]:
        limit = 100 if info.field_name == "name" else 1000
        if (
            "uk" not in value
            or set(value) - {"en", "uk"}
            or any(not v or len(v) > limit for v in value.values())
        ):
            raise ValueError("Provide bounded, nonempty en and uk translations")
        return value


class EmployeeInput(Command):
    name: str = Field(min_length=1, max_length=100)
    bio: str = Field(default="", max_length=1000)
    user_id: UUID | None = None
    active: bool = True
    may_manage_availability: bool = False
    may_cancel_appointments: bool = False
    may_block_periods: bool = False
    may_request_time_off: bool = False


class SettingsInput(Command):
    name: str = Field(min_length=1, max_length=100)
    about: str = Field(max_length=1500)
    contacts: str = Field(max_length=1000)
    timezone: str
    slot_minutes: int = Field(ge=1, le=120)
    lead_minutes: int = Field(ge=0, le=43200)
    horizon_days: int = Field(ge=1, le=365)
    cancellation_hours: int = Field(ge=0, le=720)
    notify_admins: bool
    reminders_enabled: bool
    reminder_hours: list[int] = Field(max_length=5)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value

    @field_validator("reminder_hours")
    @classmethod
    def reminders(cls, value: list[int]) -> list[int]:
        if any(not 1 <= h <= 720 for h in value) or len(set(value)) != len(value):
            raise ValueError("Reminder hours must be unique and within 1..720")
        return sorted(value, reverse=True)


class HoursInput(Command):
    employee_id: UUID | None = None
    weekday: int = Field(ge=0, le=6)
    intervals: list[tuple[time, time]] = Field(max_length=8)
    inherit: bool = False

    @model_validator(mode="after")
    def valid_intervals(self) -> "HoursInput":
        validate_hours(self.intervals)
        if self.inherit and (self.employee_id is None or self.intervals):
            raise ValueError("Inherit applies only to an employee without explicit hours")
        return self


class ExceptionInput(Command):
    employee_id: UUID | None = None
    day: date
    intervals: list[tuple[datetime, datetime]] = Field(max_length=8)

    @field_validator("intervals")
    @classmethod
    def valid_intervals(
        cls, value: list[tuple[datetime, datetime]]
    ) -> list[tuple[datetime, datetime]]:
        for start, end in value:
            require_aware(start)
            require_aware(end)
            if end.astimezone(UTC) <= start.astimezone(UTC):
                raise ValueError("Invalid exception interval")
        value = [(start.astimezone(UTC), end.astimezone(UTC)) for start, end in value]
        ordered = sorted(value)
        if any(ordered[i - 1][1] > ordered[i][0] for i in range(1, len(ordered))):
            raise ValueError("Overlapping exceptions")
        return value


class BlockInput(Command):
    employee_id: UUID | None = None
    starts_at: datetime
    ends_at: datetime
    reason: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def valid_interval(self) -> "BlockInput":
        require_aware(self.starts_at)
        require_aware(self.ends_at)
        self.starts_at = self.starts_at.astimezone(UTC)
        self.ends_at = self.ends_at.astimezone(UTC)
        if self.ends_at <= self.starts_at:
            raise ValueError("End must follow start")
        return self


class ProfileInput(Command):
    display_name: str = Field(min_length=1, max_length=100)
    locale: Literal["uk"] = "uk"
    phone: str | None = Field(default=None, pattern=r"^\+[1-9]\d{7,14}$")
