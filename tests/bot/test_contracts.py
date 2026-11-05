from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from appointment_booking.app.bot.forms import parse_field
from appointment_booking.app.bot.texts import _TRANSLATIONS, text
from appointment_booking.app.core.exceptions import StaleSelection
from appointment_booking.app.core.security import FlowStore, RateLimiter


async def test_flow_callback_size_and_owner_binding():
    redis = AsyncMock()
    flows = FlowStore(redis)
    redis.get.return_value = None
    callback = await flows.put(100, "confirm", {"starts_at": "2030-01-07T10:00+00:00"})
    assert len(callback.encode()) <= 64
    saved = redis.set.call_args.args[1]

    async def load(key):
        return saved if key.startswith("flow:") else None

    redis.get.side_effect = load
    assert (await flows.get(100, callback))[0] == "confirm"
    with pytest.raises(StaleSelection):
        await flows.get(101, callback)
    redis.get.side_effect = None
    redis.get.return_value = None
    with pytest.raises(StaleSelection):
        await flows.get(100, callback)


async def test_atomic_rate_limit_contract():
    redis = AsyncMock()
    redis.eval.return_value = 1
    await RateLimiter(redis).check(100)
    redis.eval.assert_awaited_once()


def test_locale_keys_match_and_all_templates_render():
    from string import Formatter

    assert set(_TRANSLATIONS) == {"uk"}
    for key, template in _TRANSLATIONS["uk"].items():
        values = {field: "тест" for _, field, _, _ in Formatter().parse(template) if field}
        assert text("en", key, **values) == text("uk", key, **values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("weekday", "8"),
        ("duration_minutes", "0"),
        ("active", "maybe"),
        ("intervals", "18:00-09:00"),
        ("starts_at", "2030-01-07T10:00"),
        ("phone", "1234"),
        ("currency", "uah"),
    ],
)
def test_form_rejects_invalid_values(field, value):
    with pytest.raises(ValueError):
        parse_field(field, value)


def test_form_hours_and_aware_datetime():
    assert parse_field("intervals", "09:00-12:00,13:00-18:00") == [
        ["09:00:00", "12:00:00"],
        ["13:00:00", "18:00:00"],
    ]
    assert parse_field("active", "так") is True
    assert datetime.fromisoformat(parse_field("starts_at", "2030-01-07T10:00+02:00")).tzinfo
