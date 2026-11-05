from datetime import UTC, datetime, timedelta

from appointment_booking.app.core.exceptions import InvalidTransition, PolicyViolation
from appointment_booking.app.domain.availability import require_aware

TRANSITIONS = {
    "pending": {"confirmed", "cancelled", "expired"},
    "confirmed": {"cancelled", "completed", "no_show"},
    "cancelled": set(),
    "completed": set(),
    "no_show": set(),
    "expired": set(),
}


def transition(current: str, target: str, *, ends_at: datetime, now: datetime) -> None:
    require_aware(ends_at)
    require_aware(now)
    if target not in TRANSITIONS.get(current, set()):
        raise InvalidTransition()
    if target in {"completed", "no_show"} and now.astimezone(UTC) < ends_at.astimezone(UTC):
        raise PolicyViolation()


def check_cutoff(start: datetime, now: datetime, hours: int, *, admin: bool) -> None:
    require_aware(start)
    require_aware(now)
    if not admin and start.astimezone(UTC) - now.astimezone(UTC) < timedelta(hours=hours):
        raise PolicyViolation()
