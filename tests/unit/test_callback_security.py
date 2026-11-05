"""Token protocol tests with a Redis double; real Redis tests live in integration."""

import unittest
from string import Formatter

from appointment_booking.app.bot.texts import _TRANSLATIONS, text
from appointment_booking.app.core.exceptions import RateLimited, StaleSelection
from appointment_booking.app.core.security import FlowStore, RateLimiter


class RedisDouble:
    def __init__(self):
        self.data, self.ttls = {}, {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, *, ex):
        self.data[key], self.ttls[key] = value, ex

    async def incr(self, key):
        self.data[key] = int(self.data.get(key, 0)) + 1
        return self.data[key]

    async def expire(self, key, ttl):
        self.ttls[key] = ttl

    async def eval(self, script, key_count, key, ttl):
        return await self.incr(key)


class CallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_callback_size_owner_and_expiry(self):
        redis = RedisDouble()
        flows = FlowStore(redis)
        callback = await flows.put(100, "confirm", {"id": "appointment"})
        self.assertLessEqual(len(callback.encode()), 64)
        self.assertEqual((await flows.get(100, callback))[0], "confirm")
        self.assertEqual(redis.ttls["flow:" + callback[3:]], 3600)
        with self.assertRaises(StaleSelection):
            await flows.get(101, callback)
        del redis.data["flow:" + callback[3:]]
        with self.assertRaises(StaleSelection):
            await flows.get(100, callback)

    async def test_old_screen_invalid_after_navigation(self):
        flows = FlowStore(RedisDouble())
        old = await flows.put(100, "confirm")
        await flows.advance(100)
        new = await flows.put(100, "home")
        with self.assertRaises(StaleSelection):
            await flows.get(100, old)
        self.assertEqual((await flows.get(100, new))[0], "home")

    async def test_limiter_threshold(self):
        limiter = RateLimiter(RedisDouble())
        for _ in range(15):
            await limiter.check(100)
        with self.assertRaises(RateLimited):
            await limiter.check(100)
        await limiter.check(101)

    def test_translations_match_and_templates_format(self):
        self.assertEqual(set(_TRANSLATIONS), {"uk"})
        for locale, translations in _TRANSLATIONS.items():
            for key, template in translations.items():
                values = {field: "test" for _, field, _, _ in Formatter().parse(template) if field}
                self.assertTrue(text(locale, key, **values))
