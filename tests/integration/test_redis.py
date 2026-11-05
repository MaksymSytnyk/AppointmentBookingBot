import os

import pytest
from redis.asyncio import Redis

from appointment_booking.app.core.exceptions import RateLimited, StaleSelection
from appointment_booking.app.core.security import FlowStore, RateLimiter

pytestmark = pytest.mark.integration


async def test_real_redis_flow_ttl_owner_and_rate_atomicity():
    url = os.environ.get("TEST_REDIS_URL")
    if not url or not url.endswith("/15"):
        pytest.fail("TEST_REDIS_URL must use isolated Redis database 15")
    redis = Redis.from_url(url, decode_responses=True)
    try:
        await redis.flushdb()
        flows = FlowStore(redis)
        callback = await flows.put(1234, "book", {"service_id": "test"})
        assert (await flows.get(1234, callback))[0] == "book"
        assert 0 < await redis.ttl("flow:" + callback[3:]) <= 3600
        with pytest.raises(StaleSelection):
            await flows.get(5678, callback)
        await flows.advance(1234)
        with pytest.raises(StaleSelection):
            await flows.get(1234, callback)
        limiter = RateLimiter(redis)
        for _ in range(15):
            await limiter.check(1234)
        with pytest.raises(RateLimited):
            await limiter.check(1234)
    finally:
        await redis.aclose()
