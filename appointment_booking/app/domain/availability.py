"""Pure scheduling arithmetic: local wall-clock grids, elapsed UTC durations."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True, order=True)
class Interval:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        require_aware(self.start)
        require_aware(self.end)
        object.__setattr__(self, "start", self.start.astimezone(UTC))
        object.__setattr__(self, "end", self.end.astimezone(UTC))
        if self.end <= self.start:
            raise ValueError("Interval end must follow start")

    def overlaps(self, other: "Interval") -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, other: "Interval") -> bool:
        return self.start <= other.start and other.end <= self.end


def require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware datetime required")


def local_instants(value: datetime, zone: ZoneInfo) -> tuple[datetime, ...]:
    """Zero UTC instants for a gap, one normally, two for a fall-back fold."""
    if value.tzinfo is not None:
        raise ValueError("Expected a naive wall time")
    result = set()
    for fold in (0, 1):
        instant = value.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == value:
            result.add(instant)
    return tuple(sorted(result))


def wall_interval(day: date, opens: time, closes: time, zone: ZoneInfo) -> Interval:
    if opens >= closes or opens.tzinfo is not None or closes.tzinfo is not None:
        raise ValueError("Use increasing local times without offsets")
    start = local_instants(datetime.combine(day, opens), zone)
    end = local_instants(datetime.combine(day, closes), zone)
    if len(start) != 1 or len(end) != 1:
        raise ValueError("DST boundary needs a dated exception with explicit UTC endpoints")
    return Interval(start[0], end[0])


def intersect(left: list[Interval], right: list[Interval]) -> list[Interval]:
    return [
        Interval(max(a.start, b.start), min(a.end, b.end))
        for a in left
        for b in right
        if a.overlaps(b)
    ]


def validate_hours(hours: list[tuple[time, time]]) -> None:
    ordered = sorted(hours)
    for index, (start, end) in enumerate(ordered):
        if start.tzinfo or end.tzinfo or start >= end:
            raise ValueError("Invalid working interval")
        if index and ordered[index - 1][1] > start:
            raise ValueError("Working intervals overlap")


def available_starts(
    *,
    day: date,
    zone: ZoneInfo,
    working: list[Interval],
    occupied: list[Interval],
    duration: int,
    before: int,
    after: int,
    step: int,
    now: datetime,
    lead_minutes: int,
    horizon_days: int,
) -> list[datetime]:
    require_aware(now)
    if duration <= 0 or min(before, after, lead_minutes) < 0 or not 1 <= step <= 120:
        raise ValueError("Invalid duration, buffer, lead time or grid")
    today = now.astimezone(zone).date()
    if not today <= day <= today + timedelta(days=horizon_days):
        return []
    earliest = now.astimezone(UTC) + timedelta(minutes=lead_minutes)
    starts = []
    for minute in range(0, 24 * 60, step):
        wall = datetime.combine(day, time()) + timedelta(minutes=minute)
        for start in local_instants(wall, zone):
            end = start + timedelta(minutes=duration)
            span = Interval(start - timedelta(minutes=before), end + timedelta(minutes=after))
            if (
                start >= earliest
                and any(w.contains(span) for w in working)
                and not any(span.overlaps(busy) for busy in occupied)
            ):
                starts.append(start)
    return sorted(starts)
