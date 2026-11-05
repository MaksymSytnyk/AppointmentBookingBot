import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from appointment_booking.app.database.models import NotificationJob, NotificationLog
from appointment_booking.app.services.booking_service import BookingService
from appointment_booking.app.workers.notification_worker import NotificationWorker
from tests.integration.conftest import NOW
from tests.integration.test_bookings import command

pytestmark = pytest.mark.integration


async def test_jobs_persist_and_two_workers_do_not_double_claim(demo):
    await BookingService(demo.sessions, lambda: NOW).book(demo.customer, command(demo))
    sent = []

    async def sender(chat, body):
        sent.append((chat, body))
        return len(sent)

    workers = [NotificationWorker(demo.sessions, sender, lambda: NOW) for _ in range(2)]
    await asyncio.gather(*(worker.once() for worker in workers))
    assert len(sent) == 2
    async with demo.sessions() as session:
        logs = list(await session.scalars(select(NotificationLog)))
        assert len(logs) == 2 and all(log.outcome == "sent" for log in logs)


async def test_transient_retry_and_crash_lease_recovery(demo):
    await BookingService(demo.sessions, lambda: NOW).book(demo.customer, command(demo))

    async def fail(chat, body):
        raise OSError("network unavailable")

    worker = NotificationWorker(demo.sessions, fail, lambda: NOW)
    await worker.once()
    async with demo.sessions() as session:
        attempted = await session.scalar(
            select(NotificationJob).where(NotificationJob.attempts == 1)
        )
        assert attempted.state == "queued" and attempted.next_attempt_at > NOW
    delivery = await worker.claim()
    assert delivery is not None
    later = NotificationWorker(demo.sessions, fail, lambda: NOW + timedelta(minutes=2))
    # Other due jobs may be claimed first; eventually the crashed lease is reclaimed.
    deliveries = []
    for _ in range(4):
        claimed = await later.claim()
        if claimed:
            deliveries.append(claimed)
    recovered = next(d for d in deliveries if d.job_id == delivery.job_id)
    assert recovered.token != delivery.token
    await worker.finish(delivery, "sent", 123)
    async with demo.sessions() as session:
        job = await session.get(NotificationJob, delivery.job_id)
        assert job.state == "sending" and job.lease_token == recovered.token


async def test_cancel_supersedes_old_reminders(demo):
    bookings = BookingService(demo.sessions, lambda: NOW)
    item = await bookings.book(demo.customer, command(demo))
    await bookings.change(demo.customer, item.id, version=1, action="cancelled")
    async with demo.sessions() as session:
        jobs = list(
            await session.scalars(
                select(NotificationJob).where(NotificationJob.kind.like("reminder_%"))
            )
        )
        assert jobs and all(j.state == "superseded" for j in jobs)


async def test_send_success_before_ack_can_duplicate_after_lease_expiry(demo):
    await BookingService(demo.sessions, lambda: NOW).book(demo.customer, command(demo))
    sent = []

    async def sender(chat, body):
        sent.append((chat, body))
        return len(sent)

    worker = NotificationWorker(demo.sessions, sender, lambda: NOW)
    delivery = await worker.claim()
    await sender(delivery.chat_id, delivery.body)
    # Simulate crash: no finish/ack. A restarted worker eventually reclaims the lease.
    restarted = NotificationWorker(demo.sessions, sender, lambda: NOW + timedelta(minutes=2))
    for _ in range(4):
        await restarted.once()
    assert sent.count((delivery.chat_id, delivery.body)) == 2


async def test_permanent_failure_becomes_dead(demo):
    from aiogram.exceptions import TelegramForbiddenError
    from aiogram.methods import SendMessage

    await BookingService(demo.sessions, lambda: NOW).book(demo.customer, command(demo))

    async def sender(chat, body):
        raise TelegramForbiddenError(method=SendMessage(chat_id=chat, text=body), message="Blocked")

    await NotificationWorker(demo.sessions, sender, lambda: NOW).once()
    async with demo.sessions() as session:
        job = await session.scalar(select(NotificationJob).where(NotificationJob.attempts == 1))
        assert job.state == "dead"
        log = await session.scalar(select(NotificationLog).where(NotificationLog.job_id == job.id))
        assert log.outcome == "permanent"


async def test_retry_after_is_respected(demo):
    from aiogram.exceptions import TelegramRetryAfter
    from aiogram.methods import SendMessage

    await BookingService(demo.sessions, lambda: NOW).book(demo.customer, command(demo))

    async def sender(chat, body):
        raise TelegramRetryAfter(
            method=SendMessage(chat_id=chat, text=body), message="Wait", retry_after=120
        )

    await NotificationWorker(demo.sessions, sender, lambda: NOW).once()
    async with demo.sessions() as session:
        job = await session.scalar(select(NotificationJob).where(NotificationJob.attempts == 1))
        assert job.state == "queued" and job.next_attempt_at >= NOW + timedelta(seconds=120)
