import unittest
from uuid import uuid4

from appointment_booking.app.core.exceptions import PermissionDenied
from appointment_booking.app.database.models import Appointment, Employee, User
from appointment_booking.app.domain.access import Actor


class AccessTests(unittest.TestCase):
    def actor(self, role="CUSTOMER", *, employee_id=None, permitted=False):
        user = User(id=uuid4(), telegram_id=101, display_name="User", locale="uk", active=True)
        employee = (
            Employee(
                id=employee_id or uuid4(),
                user_id=user.id,
                name="Employee",
                may_manage_availability=permitted,
            )
            if role == "BARBER"
            else None
        )
        return Actor(user, frozenset({role}) if role != "CUSTOMER" else frozenset(), employee)

    def test_read_owner_employee_admin_and_stranger(self):
        owner, employee, admin, stranger = (
            self.actor(role) for role in ("CUSTOMER", "BARBER", "OWNER", "CUSTOMER")
        )
        appointment = Appointment(customer_id=owner.user.id, employee_id=employee.employee.id)
        for actor in (owner, employee, admin):
            actor.read(appointment)
        with self.assertRaises(PermissionDenied):
            stranger.read(appointment)

    def test_employee_cannot_read_another_employee(self):
        actor = self.actor("BARBER")
        with self.assertRaises(PermissionDenied):
            actor.read(Appointment(customer_id=uuid4(), employee_id=uuid4()))

    def test_availability_scope_and_explicit_grant(self):
        permitted, restricted, admin = (
            self.actor("BARBER", permitted=True),
            self.actor("BARBER"),
            self.actor("OWNER"),
        )
        permitted.manage_availability(permitted.employee.id)
        admin.manage_availability(None)
        for actor, target in (
            (permitted, None),
            (permitted, uuid4()),
            (restricted, restricted.employee.id),
        ):
            with self.assertRaises(PermissionDenied):
                actor.manage_availability(target)

    def test_admin_required(self):
        self.actor("OWNER").require_admin()
        for role in ("CUSTOMER", "BARBER"):
            with self.assertRaises(PermissionDenied):
                self.actor(role).require_admin()
