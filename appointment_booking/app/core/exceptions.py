class BusinessError(Exception):
    code = "invalid"


class SlotUnavailable(BusinessError):
    code = "unavailable"


class PolicyViolation(BusinessError):
    code = "policy"


class PermissionDenied(BusinessError):
    code = "forbidden"


class NotFound(BusinessError):
    code = "missing"


class InvalidTransition(BusinessError):
    code = "transition"


class StaleSelection(BusinessError):
    code = "stale"


class RateLimited(BusinessError):
    code = "rate"


class ScheduleConflict(PolicyViolation):
    code = "schedule"

    def __init__(self, references: list[str]) -> None:
        super().__init__()
        self.references = tuple(references[:20])
