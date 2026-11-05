from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def __call__(self) -> datetime: ...


def utcnow() -> datetime:
    return datetime.now(UTC)
