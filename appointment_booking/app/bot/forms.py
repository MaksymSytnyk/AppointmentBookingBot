from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from aiogram.fsm.state import State, StatesGroup

from appointment_booking.app.domain.availability import require_aware, validate_hours


class Form(StatesGroup):
    input = State()
    review = State()


FIELDS: dict[str, list[str]] = {
    "time_off": ["starts_at", "ends_at", "reason"],
    "barber_date": ["schedule_day"],
    "owner_date": ["schedule_day"],
    "service": [
        "name_uk",
        "description_uk",
        "duration_minutes",
        "buffer_before",
        "buffer_after",
        "price_minor",
        "currency",
        "active",
    ],
    "employee": [
        "name",
        "bio",
        "active",
        "may_manage_availability",
        "may_cancel_appointments",
        "may_block_periods",
        "may_request_time_off",
    ],
    "settings": [
        "name",
        "about",
        "contacts",
        "timezone",
        "slot_minutes",
        "lead_minutes",
        "horizon_days",
        "cancellation_hours",
        "notify_admins",
        "reminders_enabled",
        "reminder_hours",
    ],
    "hours": ["employee_id", "weekday", "intervals"],
    "exceptions": ["employee_id", "day", "exception_intervals"],
    "blocks": ["employee_id", "starts_at", "ends_at", "reason"],
    "holidays": ["day", "label"],
    "customers": ["term"],
    "stats": ["first", "last"],
    "profile": ["display_name", "phone"],
    "reason": ["reason"],
}
FIELDS["employee_create"] = FIELDS["employee"] + ["service_ids", "weekdays", "intervals"]

BOOLS = {
    "may_block_periods",
    "may_request_time_off",
    "may_cancel_appointments",
    "active",
    "may_manage_availability",
    "notify_admins",
    "reminders_enabled",
}
INTEGERS = {
    "duration_minutes": (1, 1440),
    "buffer_before": (0, 1440),
    "buffer_after": (0, 1440),
    "price_minor": (0, 100_000_000),
    "slot_minutes": (1, 120),
    "lead_minutes": (0, 43200),
    "horizon_days": (1, 365),
    "cancellation_hours": (0, 720),
    "weekday": (0, 6),
}


def parse_field(field: str, value: str) -> Any:
    value = value.strip()
    if field == "weekdays":
        days = sorted(set(int(day.strip()) - 1 for day in value.split(",")))
        if not days or any(day not in range(7) for day in days):
            raise ValueError("weekdays")
        return days
    if field == "service_ids":
        return [str(UUID(item.strip())) for item in value.split(",")]
    if field in BOOLS:
        if value.lower() not in {"yes", "no", "так", "ні"}:
            raise ValueError("boolean")
        return value.lower() in {"yes", "так"}
    if field in INTEGERS:
        integer = int(value)
        low, high = INTEGERS[field]
        if not low <= integer <= high:
            raise ValueError("range")
        return integer
    if field in {"user_id", "employee_id"}:
        return None if value == "-" else str(UUID(value))
    if field in {"day", "first", "last", "schedule_day"}:
        return date.fromisoformat(value).isoformat()
    if field in {"starts_at", "ends_at"}:
        instant = datetime.fromisoformat(value)
        require_aware(instant)
        return instant.isoformat()
    if field == "intervals":
        if value in {"-", "успадкувати"}:
            return "inherit" if value == "успадкувати" else value
        intervals = [
            tuple(time.fromisoformat(part) for part in item.split("-")) for item in value.split(",")
        ]
        if any(len(pair) != 2 for pair in intervals):
            raise ValueError("intervals")
        validate_hours(intervals)  # type: ignore[arg-type]
        return [[a.isoformat(), b.isoformat()] for a, b in intervals]
    if field == "exception_intervals":
        if value == "-":
            return []
        result = []
        for item in value.split(","):
            start, end = (datetime.fromisoformat(v.strip()) for v in item.split("/"))
            require_aware(start)
            require_aware(end)
            if end <= start:
                raise ValueError("interval")
            result.append([start.isoformat(), end.isoformat()])
        return result
    if field == "reminder_hours":
        reminder_values = [] if value == "-" else [int(v) for v in value.split(",")]
        if (
            len(reminder_values) > 5
            or len(set(reminder_values)) != len(reminder_values)
            or any(not 1 <= h <= 720 for h in reminder_values)
        ):
            raise ValueError("reminders")
        return reminder_values
    if field == "locale" and value != "uk":
        raise ValueError("locale")
    if field == "phone":
        from appointment_booking.app.domain.dto import ProfileInput

        return ProfileInput(display_name="validation", phone=None if value == "-" else value).phone
    if field == "currency" and (len(value) != 3 or not value.isalpha() or not value.isupper()):
        raise ValueError("currency")
    if field == "timezone":
        from zoneinfo import ZoneInfo

        ZoneInfo(value)
    limit = (
        1500
        if field == "about"
        else 1000
        if field.startswith("description") or field in {"bio", "contacts"}
        else 300
        if field == "reason"
        else 100
    )
    if value == "-" and field == "bio":
        return ""
    if not 1 <= len(value) <= limit:
        raise ValueError("length")
    return value
