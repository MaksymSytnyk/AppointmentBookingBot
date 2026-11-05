import logging
import unittest

from pydantic import ValidationError

from appointment_booking.app.core.config import Settings
from appointment_booking.app.core.logging import SafeFormatter
from appointment_booking.app.domain.dto import ProfileInput, ServiceInput, SettingsInput


class ValidationTests(unittest.TestCase):
    def test_secrets_are_hidden(self):
        settings = Settings(
            _env_file=None,
            bot_token="123456:secret",
            database_url="postgresql+asyncpg://user:private@localhost/database",
        )
        self.assertNotIn("secret", repr(settings))
        self.assertNotIn("private", repr(settings))

    def test_database_driver_validation(self):
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, database_url="sqlite:///local")

    def test_phone_is_e164(self):
        self.assertEqual(
            ProfileInput(display_name="Test", phone="+380501234567").phone, "+380501234567"
        )
        for phone in ("username", "1234", "+0000000000"):
            with self.assertRaises(ValidationError):
                ProfileInput(display_name="Test", phone=phone)

    def test_service_bounds_and_localization(self):
        values = dict(
            name={"en": "Cut", "uk": "Стрижка"},
            description={"en": "Description", "uk": "Опис"},
            duration_minutes=60,
            price_minor=80000,
        )
        ServiceInput(**values)
        for changed in (
            {"duration_minutes": 0},
            {"buffer_after": -1},
            {"price_minor": -1},
            {"currency": "uah"},
            {"name": {"en": "Cut"}},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValidationError):
                ServiceInput(**(values | changed))

    def test_timezone_and_reminders_validation(self):
        base = dict(
            name="Shop",
            about="About",
            contacts="Contact",
            timezone="Europe/Kyiv",
            slot_minutes=15,
            lead_minutes=60,
            horizon_days=30,
            cancellation_hours=12,
            notify_admins=True,
            reminders_enabled=True,
            reminder_hours=[24, 2],
        )
        SettingsInput(**base)
        for changed in (
            {"timezone": "Mars/Olympus"},
            {"reminder_hours": [2, 2]},
            {"reminder_hours": [0]},
            {"horizon_days": 0},
        ):
            with self.assertRaises(ValidationError):
                SettingsInput(**(base | changed))

    def test_log_redacts_token_and_database_credentials(self):
        record = logging.LogRecord(
            "test",
            40,
            "",
            1,
            "failure 123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ1234 postgresql+asyncpg://a:secret@host/db",
            (),
            None,
        )
        result = SafeFormatter().format(record)
        self.assertNotIn("ABCDEFGHIJKLMNOPQRSTUVWXYZ1234", result)
        self.assertNotIn("secret", result)
