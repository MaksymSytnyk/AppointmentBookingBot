"""Aiogram transport tests; services are doubles, never concurrency evidence."""

from datetime import UTC, datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, EditMessageText, GetMe, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from appointment_booking.app.bot.dependencies import Services
from appointment_booking.app.bot.router import build_router
from appointment_booking.app.core.security import FlowStore, RateLimiter
from appointment_booking.app.database.models import Appointment, Employee, Service
from appointment_booking.app.database.models import User as Customer
from appointment_booking.app.domain.access import Actor
from appointment_booking.app.services.availability_service import Slot


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.messages = []

    async def close(self):
        return None

    async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109
        if isinstance(method, AnswerCallbackQuery):
            return True
        if isinstance(method, GetMe):
            return User(id=123456, is_bot=True, first_name="Test bot")
        assert isinstance(method, (SendMessage, EditMessageText)), type(method)
        message = Message(
            message_id=len(self.messages) + 1,
            date=datetime.now(UTC),
            chat=Chat(id=100, type="private"),
            text=method.text,
        )
        # Preserve the outgoing keyboard for assertions; Telegram only echoes inline markup.
        message = message.model_copy(update={"reply_markup": method.reply_markup})
        self.messages.append(message)
        return message

    async def stream_content(
        self,
        url,
        headers=None,
        timeout=30,  # noqa: ASYNC109
        chunk_size=65536,
        raise_for_status=True,
    ):
        yield b""


async def test_entire_customer_booking_navigation_and_explicit_confirmation():
    redis, cache = AsyncMock(), {}

    async def save(key, value, **kwargs):
        cache[key] = value
        return True

    async def load(key):
        return cache.get(key)

    async def increment(key):
        cache[key] = int(cache.get(key, 0)) + 1
        return cache[key]

    redis.incr.side_effect = increment
    redis.set.side_effect, redis.get.side_effect, redis.eval.return_value = save, load, 1
    services = Services(*(AsyncMock() for _ in range(5)))
    customer = Customer(id=uuid4(), telegram_id=100, display_name="Test", locale="uk", active=True)
    services.users.identify.return_value = Actor(customer, frozenset(), None)
    services.catalog.settings.return_value = SimpleNamespace(
        name="Demo", timezone="UTC", horizon_days=30, cancellation_hours=12, revision=1
    )
    service = Service(
        id=uuid4(),
        name={"en": "Cut", "uk": "Стрижка"},
        description={"en": "Precision", "uk": "Точність"},
        duration_minutes=60,
        price_minor=80000,
        currency="UAH",
        revision=1,
        active=True,
    )
    employee = Employee(id=uuid4(), name="Stylist")
    services.catalog.services.return_value = [service]
    services.catalog.service.return_value = service
    services.catalog.employees.return_value = [employee]

    async def slots(user_id, service_id, day, *args):
        return [Slot(employee.id, employee.name, datetime.combine(day, time(10), UTC))]

    services.availability.slots.side_effect = slots
    appointment = Appointment(
        id=uuid4(),
        reference="DEMO123",
        status="confirmed",
        starts_at=datetime(2030, 1, 7, 10, tzinfo=UTC),
        service_label={"uk": "Стрижка"},
    )
    services.bookings.book.return_value = appointment
    services.bookings.detail.return_value = (appointment, employee.name, customer.display_name)
    session = RecordingSession()
    bot = Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_CREDENTIAL", session=session)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(build_router(services, FlowStore(redis), RateLimiter(redis)))
    user = User(id=100, first_name="Test", is_bot=False, language_code="en")
    counter = 1

    async def press(label=None):
        nonlocal counter
        if not hasattr(session.messages[-1].reply_markup, "inline_keyboard"):
            counter += 1
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=counter,
                    message=Message(
                        message_id=counter,
                        date=datetime.now(UTC),
                        chat=Chat(id=100, type="private"),
                        from_user=user,
                        text=label,
                    ),
                ),
            )
            return
        buttons = [
            button for row in session.messages[-1].reply_markup.inline_keyboard for button in row
        ]
        button = next(b for b in buttons if b.text.startswith(label)) if label else buttons[0]
        counter += 1
        query = CallbackQuery(
            id=str(counter),
            from_user=user,
            chat_instance="test",
            data=button.callback_data,
            message=session.messages[-1],
        )
        await dispatcher.feed_update(bot, Update(update_id=counter, callback_query=query))

    try:
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=1,
                message=Message(
                    message_id=1,
                    date=datetime.now(UTC),
                    chat=Chat(id=100, type="private"),
                    from_user=user,
                    text="/start",
                ),
            ),
        )
        await press("✂️ Записатися")
        await press("Стрижка")
        await press("✂️ Записатися")
        await press("Будь-який вільний барбер")
        await press()  # first available date
        await press()  # first available time
        assert "Перевір деталі" in session.messages[-1].text
        assert "Stylist" in session.messages[-1].text
        services.bookings.book.assert_not_awaited()
        await press("Підтвердити")
        services.bookings.book.assert_awaited_once()
        command = services.bookings.book.call_args.args[1]
        assert command.employee_id == employee.id and command.service_id == service.id
        assert command.starts_at.tzinfo is not None
        assert "DEMO123" in session.messages[-1].text
    finally:
        await dispatcher.storage.close()
        await bot.session.close()
