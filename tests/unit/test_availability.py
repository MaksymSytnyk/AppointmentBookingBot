import unittest
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from appointment_booking.app.domain.availability import (
    Interval,
    available_starts,
    intersect,
    local_instants,
    validate_hours,
    wall_interval,
)


class AvailabilityTests(unittest.TestCase):
    day = date(2026, 11, 5)
    zone = ZoneInfo("UTC")

    def slots(self, **overrides):
        args = dict(
            day=self.day,
            zone=self.zone,
            working=[wall_interval(self.day, time(9), time(18), self.zone)],
            occupied=[],
            duration=60,
            before=0,
            after=15,
            step=15,
            now=datetime(2026, 11, 4, tzinfo=UTC),
            lead_minutes=60,
            horizon_days=30,
        )
        args.update(overrides)
        return available_starts(**args)

    def instant(self, hour, minute=0):
        return datetime.combine(self.day, time(hour, minute), UTC)

    def test_duration_and_buffer_must_fit(self):
        slots = self.slots()
        self.assertEqual(slots[0], self.instant(9))
        self.assertEqual(slots[-1], self.instant(16, 45))
        self.assertNotIn(self.instant(17), slots)

    def test_overlap_touching_and_both_buffers(self):
        busy = Interval(self.instant(10, 45), self.instant(12, 15))
        slots = self.slots(occupied=[busy], before=15)
        self.assertIn(self.instant(9, 30), slots)
        self.assertNotIn(self.instant(9, 45), slots)
        self.assertNotIn(self.instant(12, 15), slots)
        self.assertIn(self.instant(12, 30), slots)

    def test_lunch_and_holiday(self):
        split = [
            wall_interval(self.day, time(9), time(12), self.zone),
            wall_interval(self.day, time(13), time(18), self.zone),
        ]
        self.assertNotIn(self.instant(11), self.slots(working=split))
        self.assertEqual(self.slots(working=[]), [])

    def test_horizon_and_lead_boundaries(self):
        now = self.instant(10)
        slots = self.slots(now=now)
        self.assertEqual(slots[0], self.instant(11))
        self.assertEqual(self.slots(day=self.day + timedelta(days=31), now=now), [])
        self.assertEqual(self.slots(day=self.day - timedelta(days=1), now=now), [])

    def test_exact_duration_fit(self):
        self.assertIn(self.instant(17), self.slots(after=0))

    def test_non_utc_timezone(self):
        zone = ZoneInfo("Asia/Kathmandu")
        start = wall_interval(self.day, time(9), time(18), zone).start
        self.assertEqual(start.hour, 3)
        self.assertEqual(start.minute, 15)

    def test_dst_gap_and_fold(self):
        zone = ZoneInfo("America/New_York")
        self.assertEqual(local_instants(datetime(2026, 3, 8, 2, 30), zone), ())
        folds = local_instants(datetime(2026, 11, 1, 1, 30), zone)
        self.assertEqual(len(folds), 2)
        self.assertEqual(folds[1] - folds[0], timedelta(hours=1))
        with self.assertRaises(ValueError):
            wall_interval(date(2026, 11, 1), time(1, 30), time(3), zone)

    def test_invalid_inputs(self):
        for override in (
            {"duration": 0},
            {"before": -1},
            {"step": 0},
            {"now": datetime(2026, 11, 4)},
        ):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.slots(**override)
        for hours in ([(time(18), time(9))], [(time(9), time(12)), (time(11), time(13))]):
            with self.assertRaises(ValueError):
                validate_hours(hours)

    def test_intersection(self):
        a = Interval(self.instant(9), self.instant(12))
        b = Interval(self.instant(11), self.instant(13))
        self.assertEqual(intersect([a], [b]), [Interval(self.instant(11), self.instant(12))])

    def test_interval_compares_fold_instants_in_utc(self):
        zone = ZoneInfo("America/New_York")
        start = datetime(2026, 11, 1, 1, 50, tzinfo=zone, fold=0)
        end = datetime(2026, 11, 1, 1, 10, tzinfo=zone, fold=1)
        interval = Interval(start, end)
        self.assertEqual(interval.end - interval.start, timedelta(minutes=20))
