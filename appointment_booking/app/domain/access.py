from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from appointment_booking.app.core.exceptions import PermissionDenied

if TYPE_CHECKING:
    from appointment_booking.app.database.models import Appointment, Employee, User


@dataclass(frozen=True)
class Actor:
    user: User
    roles: frozenset[str]
    employee: Employee | None

    @property
    def admin(self) -> bool:
        return "OWNER" in self.roles

    def require_admin(self) -> None:
        if not self.admin:
            raise PermissionDenied()

    def require_barber(self) -> None:
        if self.employee is None or not self.employee.active:
            raise PermissionDenied()

    @property
    def primary_role(self) -> str:
        return "OWNER" if self.admin else "BARBER" if self.employee else "CUSTOMER"

    def read(self, appointment: Appointment) -> None:
        if (
            not self.admin
            and appointment.customer_id != self.user.id
            and (self.employee is None or appointment.employee_id != self.employee.id)
        ):
            raise PermissionDenied()

    def manage_availability(self, employee_id: UUID | None) -> None:
        if not self.admin and not (
            self.employee
            and self.employee.id == employee_id
            and self.employee.may_manage_availability
        ):
            raise PermissionDenied()

    def manage_blocks(self, employee_id: UUID | None) -> None:
        if not self.admin and not (
            self.employee and self.employee.id == employee_id and self.employee.may_block_periods
        ):
            raise PermissionDenied()
