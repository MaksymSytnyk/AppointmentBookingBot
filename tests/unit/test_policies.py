import unittest
from datetime import UTC, datetime, timedelta

from appointment_booking.app.core.exceptions import InvalidTransition, PolicyViolation
from appointment_booking.app.domain.policies import TRANSITIONS, check_cutoff, transition


class PolicyTests(unittest.TestCase):
    now = datetime(2026, 11, 4, tzinfo=UTC)

    def test_cutoff_boundary_and_override(self):
        check_cutoff(self.now + timedelta(hours=12), self.now, 12, admin=False)
        check_cutoff(self.now, self.now, 12, admin=True)
        with self.assertRaises(PolicyViolation):
            check_cutoff(
                self.now + timedelta(hours=12) - timedelta(seconds=1), self.now, 12, admin=False
            )

    def test_every_transition(self):
        for current in TRANSITIONS:
            for target in TRANSITIONS:
                if target in TRANSITIONS[current]:
                    transition(current, target, ends_at=self.now, now=self.now)
                else:
                    with self.assertRaises(InvalidTransition):
                        transition(current, target, ends_at=self.now, now=self.now)

    def test_cannot_complete_before_end(self):
        with self.assertRaises(PolicyViolation):
            transition(
                "confirmed", "completed", ends_at=self.now + timedelta(minutes=1), now=self.now
            )

    def test_cutoff_uses_elapsed_time_across_dst(self):
        from zoneinfo import ZoneInfo

        zone = ZoneInfo("America/New_York")
        now = datetime(2026, 11, 1, 0, 30, tzinfo=zone)
        start = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
        # Two elapsed hours despite only one wall-clock hour.
        check_cutoff(start, now, 2, admin=False)
