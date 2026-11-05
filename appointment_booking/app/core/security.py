from __future__ import annotations

import json
import secrets
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from redis.asyncio import Redis

from appointment_booking.app.core.exceptions import RateLimited, StaleSelection

RATE_SCRIPT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return n
"""
RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""
RENEW_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('EXPIRE', KEYS[1], ARGV[2]) end
return 0
"""


class RateLimiter:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def check(self, telegram_id: int) -> None:
        count = await cast(Any, self.redis).eval(RATE_SCRIPT, 1, f"rate:{telegram_id}", "10")
        if int(count) > 15:
            raise RateLimited()


class FlowStore:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def view(self, user_id: int, default: str) -> str:
        return str(await self.redis.get(f"role-view:{user_id}") or default)

    async def set_view(self, user_id: int, role: str) -> None:
        await self.redis.set(f"role-view:{user_id}", role, ex=7200)

    async def advance(self, user_id: int) -> None:
        key = f"flow-generation:{user_id}"
        await self.redis.incr(key)
        await self.redis.expire(key, 7200)

    async def put(
        self,
        user_id: int,
        action: str,
        data: dict[str, Any] | None = None,
        *,
        token: str | None = None,
    ) -> str:
        token = token or secrets.token_urlsafe(16)
        generation = int(await self.redis.get(f"flow-generation:{user_id}") or 0)
        value = json.dumps(
            {"owner": user_id, "generation": generation, "action": action, "data": data or {}}
        )
        await self.redis.set(f"flow:{token}", value, ex=3600)
        return "v1:" + token

    async def get(self, user_id: int, callback: str) -> tuple[str, dict[str, Any]]:
        if not callback.startswith("v1:") or len(callback.encode()) > 64:
            raise StaleSelection()
        value = await self.redis.get("flow:" + callback[3:])
        if value is None:
            raise StaleSelection()
        payload = json.loads(value)
        if payload["owner"] != user_id:
            raise StaleSelection()
        generation = int(await self.redis.get(f"flow-generation:{user_id}") or 0)
        if payload.get("generation") != generation:
            raise StaleSelection()
        return str(payload["action"]), dict(payload["data"])
