import asyncio
import sys
from pathlib import Path
from time import time

from redis.asyncio import Redis
from sqlalchemy import text

from appointment_booking.app.core.config import get_settings
from appointment_booking.app.database.session import create_database


async def dependencies() -> None:
    settings = get_settings()
    engine, sessions = create_database(settings.database_url.get_secret_value())
    redis = Redis.from_url(settings.redis_url.get_secret_value())
    try:
        await redis.ping()
        async with sessions() as session:
            await session.execute(text("SELECT 1"))
    finally:
        await redis.aclose()
        await engine.dispose()


def main() -> None:
    kind = "worker" if "--worker" in sys.argv else "bot"
    path = Path(f"/tmp/atelier-{kind}.health")
    if not path.exists() or time() - float(path.read_text()) > 30:
        raise SystemExit(1)
    try:
        asyncio.run(asyncio.wait_for(dependencies(), timeout=3))
    except Exception:
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
