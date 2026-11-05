import asyncio
import logging
import signal
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand
from redis.asyncio import Redis

from appointment_booking.app.bot.dependencies import Services
from appointment_booking.app.bot.router import build_router
from appointment_booking.app.bot.texts import text
from appointment_booking.app.core.config import get_settings
from appointment_booking.app.core.logging import setup_logging
from appointment_booking.app.core.security import (
    RELEASE_SCRIPT,
    RENEW_SCRIPT,
    FlowStore,
    RateLimiter,
)
from appointment_booking.app.database.session import create_database
from appointment_booking.app.services.admin_service import AdminService
from appointment_booking.app.services.availability_service import AvailabilityService
from appointment_booking.app.services.booking_service import BookingService
from appointment_booking.app.services.catalog_service import CatalogService
from appointment_booking.app.services.user_service import UserService
from appointment_booking.app.workers.notification_worker import NotificationWorker, telegram_sender

logger = logging.getLogger(__name__)


async def run(*, worker: bool = False) -> None:
    import secrets

    settings = get_settings()
    setup_logging(settings.log_level)
    token = settings.require_token()
    engine, sessions = create_database(settings.database_url.get_secret_value())
    redis = Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)
    bot = Bot(token, default=DefaultBotProperties(parse_mode="HTML"))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    owner = secrets.token_hex(16)
    lease_key = "atelier:polling-leader"
    tasks: list[asyncio.Task[None]] = []
    storage = RedisStorage(redis=redis, state_ttl=3600, data_ttl=3600)
    try:
        await redis.ping()
        async with sessions() as session:
            from appointment_booking.app.database.repositories import CatalogRepository

            await CatalogRepository(session).settings()
        if not worker and not await redis.set(lease_key, owner, nx=True, ex=30):
            raise RuntimeError("Another bot process holds the polling lease")

        async def heartbeat() -> None:
            while not stop.is_set():
                if not worker:
                    try:
                        if not await redis.eval(RENEW_SCRIPT, 1, lease_key, owner, 30):
                            stop.set()
                            return
                    except Exception:
                        stop.set()
                        return
                from pathlib import Path
                from time import time

                path = Path("/tmp/atelier-worker.health" if worker else "/tmp/atelier-bot.health")
                await asyncio.to_thread(path.write_text, str(time()))
                try:
                    await asyncio.wait_for(stop.wait(), 5)
                except TimeoutError:
                    pass

        tasks.append(asyncio.create_task(heartbeat()))
        if worker:
            await NotificationWorker(sessions, telegram_sender(bot)).run(stop)
        else:
            await bot.set_my_name(name="ATELIER BARBER")
            await bot.set_my_description(description=text("uk", "bot_description"))
            await bot.set_my_short_description(
                short_description=text("uk", "bot_short_description")
            )
            await bot.set_my_commands(
                commands=[
                    BotCommand(command=key, description=text("uk", "command_" + key))
                    for key in ("start", "menu", "cancel")
                ]
            )
            services = Services(
                UserService(
                    sessions,
                    [settings.owner_telegram_id]
                    if settings.owner_telegram_id
                    else settings.admin_ids,
                ),
                CatalogService(sessions),
                AvailabilityService(sessions),
                BookingService(sessions),
                AdminService(sessions),
            )
            dispatcher = Dispatcher(storage=storage, events_isolation=storage.create_isolation())
            dispatcher.include_router(build_router(services, FlowStore(redis), RateLimiter(redis)))
            polling = asyncio.create_task(
                dispatcher.start_polling(
                    bot,
                    handle_signals=False,
                    close_bot_session=False,
                    allowed_updates=dispatcher.resolve_used_update_types(),
                )
            )
            stopped = asyncio.create_task(stop.wait())
            done, _ = await asyncio.wait([polling, stopped], return_when=asyncio.FIRST_COMPLETED)
            if polling in done:
                stopped.cancel()
                await polling
            else:
                await dispatcher.stop_polling()
                await polling
            with suppress(asyncio.CancelledError):
                await stopped
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if not worker:
            with suppress(Exception):
                await redis.eval(RELEASE_SCRIPT, 1, lease_key, owner)
        await bot.session.close()
        await storage.close()
        await engine.dispose()


def main() -> None:
    import sys

    try:
        asyncio.run(run(worker="--worker" in sys.argv))
    except Exception as exc:
        logger.error("startup_failed category=%s", type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
