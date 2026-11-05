import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from appointment_booking.app.bot.texts import text
from appointment_booking.app.core.clock import Clock, utcnow
from appointment_booking.app.database.models import NotificationLog
from appointment_booking.app.database.repositories import (
    AppointmentRepository,
    CatalogRepository,
    NotificationRepository,
    UserRepository,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Delivery:
    job_id: UUID
    token: UUID
    attempt: int
    chat_id: int
    body: str


class NotificationWorker:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        send: Callable[[int, str], Awaitable[int]],
        clock: Clock = utcnow,
    ) -> None:
        self.sessions, self.send, self.clock = sessions, send, clock

    async def claim(self) -> Delivery | None:
        async with self.sessions.begin() as session:
            repo = NotificationRepository(session)
            now = self.clock()
            job = await repo.claim(now)
            if job is None:
                return None
            if job.attempts and job.state == "sending":
                old = await repo.attempt(job.id, job.attempts)
                if old and old.outcome == "started":
                    old.outcome = "unknown_after_crash"
            if job.state == "sending" and job.attempts and job.attempts % 8 == 0:
                job.state, job.lease_token, job.lease_until = "dead", None, None
                return None
            appointment = await AppointmentRepository(session).get(job.appointment_id)
            if appointment.version != job.appointment_version or (
                job.kind.startswith("reminder_") and appointment.status != "confirmed"
            ):
                job.state, job.lease_token, job.lease_until = "superseded", None, None
                return None
            user = await UserRepository(session).recipient(job.recipient_id)
            if user is None or not user.active:
                job.attempts += 1
                job.state, job.lease_token, job.lease_until = "dead", None, None
                session.add(
                    NotificationLog(
                        job_id=job.id, attempt=job.attempts, outcome="inactive_recipient"
                    )
                )
                return None
            settings = await CatalogRepository(session).settings()
            employee = await CatalogRepository(session).employee(appointment.employee_id)
            job.state = "sending"
            job.attempts += 1
            job.lease_token = uuid4()
            # send is bounded to 25 seconds; lease is deliberately much longer.
            job.lease_until = now + timedelta(seconds=90)
            session.add(NotificationLog(job_id=job.id, attempt=job.attempts, outcome="started"))
            locale = user.locale
            local = appointment.starts_at.astimezone(ZoneInfo(settings.timezone))
            kind = "reminder" if job.kind.startswith("reminder_") else job.kind
            body = "\n".join(
                [
                    text(locale, "notice_" + kind),
                    appointment.reference,
                    appointment.service_label.get("uk", "Послуга"),
                    employee.name,
                    local.strftime("%Y-%m-%d %H:%M %z"),
                    settings.name,
                ]
            )
            return Delivery(job.id, job.lease_token, job.attempts, user.telegram_id, body)

    async def finish(
        self, delivery: Delivery, outcome: str, message_id: int | None = None, retry_after: int = 0
    ) -> None:
        async with self.sessions.begin() as session:
            repo = NotificationRepository(session)
            job = await repo.fenced(delivery.job_id, delivery.token)
            if job is None:
                return
            log = await repo.attempt(job.id, delivery.attempt)
            if log:
                log.outcome, log.telegram_message_id = outcome, message_id
            if outcome == "sent":
                job.state = "sent"
            elif outcome == "permanent" or job.attempts % 8 == 0:
                job.state = "dead"
            else:
                job.state = "queued"
                delay = max(
                    retry_after, min(3600, 2 ** min(job.attempts, 11)) + secrets.randbelow(5)
                )
                job.next_attempt_at = self.clock() + timedelta(seconds=delay)
            job.lease_token, job.lease_until = None, None

    async def once(self) -> bool:
        delivery = await self.claim()
        if delivery is None:
            return False
        try:
            message_id = await asyncio.wait_for(
                self.send(delivery.chat_id, delivery.body), timeout=25
            )
        except TelegramRetryAfter as exc:
            await self.finish(delivery, "rate_limited", retry_after=exc.retry_after)
        except (TelegramForbiddenError, TelegramBadRequest):
            await self.finish(delivery, "permanent")
        except (TelegramNetworkError, TelegramServerError, TimeoutError, OSError):
            await self.finish(delivery, "transient")
        else:
            await self.finish(delivery, "sent", message_id)
        return True

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                worked = await self.once()
            except Exception as exc:
                logger.error("worker_failed category=%s", type(exc).__name__)
                worked = False
            if not worked:
                try:
                    await asyncio.wait_for(stop.wait(), 2)
                except TimeoutError:
                    pass


def telegram_sender(bot: Bot) -> Callable[[int, str], Awaitable[int]]:
    async def send(chat_id: int, body: str) -> int:
        message = await bot.send_message(chat_id, body, parse_mode=None)
        return message.message_id

    return send
