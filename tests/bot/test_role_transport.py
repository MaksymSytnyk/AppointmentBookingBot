"""Actual Aiogram routing with Telegram transport recording and opaque callbacks."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from appointment_booking.app.bot.dependencies import Services
from appointment_booking.app.bot.router import build_router
from appointment_booking.app.bot.texts import _TRANSLATIONS
from appointment_booking.app.core.security import FlowStore
from appointment_booking.app.database.models import Employee
from appointment_booking.app.database.models import User as DbUser
from appointment_booking.app.domain.access import Actor
from tests.bot.test_customer_transport import RecordingSession
from tests.unit.test_callback_security import RedisDouble


@pytest.fixture
async def harness():
    session = RecordingSession()
    bot = Bot("123456:TEST_ONLY_NOT_A_REAL_TELEGRAM_CREDENTIAL", session=session)
    services = Services(*(AsyncMock() for _ in range(5)))
    flows = FlowStore(RedisDouble())
    dispatcher = Dispatcher(storage=MemoryStorage())
    limiter = AsyncMock()
    dispatcher.include_router(build_router(services, flows, limiter))
    customer = DbUser(id=uuid4(), telegram_id=100, display_name="Іван", locale="uk", active=True)
    services.catalog.settings.return_value = SimpleNamespace(name="ATELIER BARBER", timezone="UTC")
    services.users.identify.return_value = Actor(customer, frozenset({"CUSTOMER"}), None)
    user = User(id=100, first_name="Іван", is_bot=False, language_code="en")

    async def send(command):
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=1,
                message=Message(
                    message_id=1,
                    date=datetime.now(UTC),
                    chat=Chat(id=100, type="private"),
                    from_user=user,
                    text=command,
                ),
            ),
        )

    async def callback(action, data=None):
        token = await flows.put(100, action, data)
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=2,
                callback_query=CallbackQuery(
                    id="2",
                    from_user=user,
                    chat_instance="test",
                    data=token,
                    message=session.messages[-1].model_copy(update={"reply_markup": None}),
                ),
            ),
        )

    yield SimpleNamespace(
        session=session,
        services=services,
        customer=customer,
        send=send,
        callback=callback,
        dispatcher=dispatcher,
        bot=bot,
    )
    await dispatcher.storage.close()
    await bot.session.close()


def labels(harness):
    return [
        button.text
        for row in (
            getattr(harness.session.messages[-1].reply_markup, "inline_keyboard", None)
            or harness.session.messages[-1].reply_markup.keyboard
        )
        for button in row
    ]


async def test_customer_menu_is_ukrainian_and_private(harness):
    await harness.send("/start")
    assert labels(harness) == [
        "✂️ Записатися",
        "📅 Мої записи",
        "💈 Послуги та ціни",
        "📍 Про барбершоп",
        "📞 Контакти",
    ]
    assert "ATELIER BARBER" in harness.session.messages[-1].text
    await harness.send("/owner")
    assert "не маєте дозволу" in harness.session.messages[-1].text
    await harness.send("/barber")
    assert "не маєте дозволу" in harness.session.messages[-1].text


@pytest.mark.parametrize(
    "action,data",
    [
        ("admin", {}),
        ("employee", {}),
        ("manage", {"kind": "employees"}),
        ("invite", {"id": str(uuid4())}),
        ("barber_settings", {}),
        ("deactivate_apply", {}),
        ("overview", {}),
        ("time_off_form", {}),
        ("time_off_list", {}),
        ("time_off_apply", {}),
    ],
)
async def test_unauthorized_callback_rejected_even_with_valid_flow(harness, action, data):
    await harness.send("/start")
    await harness.callback(action, data)
    assert "не маєте дозволу" in harness.session.messages[-1].text
    harness.services.admin.save_employee.assert_not_awaited()
    harness.services.admin.invite.assert_not_awaited()


async def test_barber_menu_and_owner_denial(harness):
    employee = Employee(id=uuid4(), user_id=harness.customer.id, name="Олександр", active=True)
    harness.services.users.identify.return_value = Actor(
        harness.customer, frozenset({"BARBER"}), employee
    )
    await harness.send("/start")
    assert labels(harness) == [
        "📅 Мій розклад",
        "🗓 Записи на сьогодні",
        "📋 Найближчі записи",
        "👤 Мій профіль",
        "⚙️ Налаштування",
    ]
    await harness.send("/owner")
    assert "не маєте дозволу" in harness.session.messages[-1].text


async def test_owner_has_private_menu_and_explicit_role_switch(harness):
    employee = Employee(id=uuid4(), name="Власник", active=True)
    harness.services.users.identify.return_value = Actor(
        harness.customer, frozenset({"OWNER", "BARBER"}), employee
    )
    await harness.send("/owner")
    assert labels(harness) == [
        "📊 Огляд бізнесу",
        "👥 Працівники",
        "✂️ Послуги",
        "📅 Усі записи",
        "🕒 Робочий графік",
        "🚫 Вихідні та недоступний час",
        "⚙️ Налаштування бізнесу",
    ]
    await harness.send("/barber")
    assert "📅 Мій розклад" in labels(harness)
    await harness.send("/customer")
    assert labels(harness)[0] == "✂️ Записатися"
    assert "👥 Працівники" not in labels(harness)


def test_only_ukrainian_resources_and_no_language_selection():
    import re
    from pathlib import Path

    assert set(_TRANSLATIONS) == {"uk"}
    assert not Path("appointment_booking/app/bot/i18n/en.json").exists()
    assert "field_locale" not in _TRANSLATIONS["uk"]
    for key, value in _TRANSLATIONS["uk"].items():
        # Placeholder names, configured IANA zones and input format examples are technical data.
        prose = re.sub(r"\{[^}]+\}", "", value)
        assert re.search(r"[А-Яа-яІіЇїЄєҐґ]", prose), key


async def test_owner_employee_wizard_reviews_before_atomic_creation(harness):
    from appointment_booking.app.database.models import Service

    harness.services.users.identify.return_value = Actor(
        harness.customer, frozenset({"OWNER"}), None
    )
    service = Service(id=uuid4(), name={"uk": "Стрижка"}, active=True)
    harness.services.admin.services.return_value = [service]
    employee = Employee(id=uuid4(), name="Олександр")
    harness.services.admin.create_employee.return_value = employee
    await harness.send("/owner")
    await harness.callback("form", {"kind": "employee_create"})
    for value in ("Олександр", "Стрижки та борода", "так", "ні", "так", "ні", "так"):
        await harness.send(value)
    assert "Які послуги" in harness.session.messages[-1].text
    await harness.callback("employee_services", {"id": str(service.id)})
    assert "✓ Стрижка" in labels(harness)
    await harness.callback("employee_services", {"done": True})
    await harness.send("1,2,3,4,5")
    await harness.send("09:00-13:00,14:00-18:00")
    assert "Перевірте значення" in harness.session.messages[-1].text
    assert "Стрижка" in harness.session.messages[-1].text
    harness.services.admin.create_employee.assert_not_awaited()
    state = harness.dispatcher.fsm.get_context(bot=harness.bot, chat_id=100, user_id=100)
    data = await state.get_data()
    await harness.callback("form_apply", {"nonce": data["nonce"]})
    harness.services.admin.create_employee.assert_awaited_once()
    args = harness.services.admin.create_employee.call_args.args
    assert args[1].name == "Олександр" and args[1].may_cancel_appointments
    assert args[2] == [service.id] and args[3] == [0, 1, 2, 3, 4]
    assert len(args[4]) == 2
    assert "Запросити барбера" in labels(harness)


async def test_role_view_persists_without_changing_permissions(harness):
    harness.services.users.identify.return_value = Actor(
        harness.customer, frozenset({"OWNER"}), None
    )
    await harness.send("/customer")
    await harness.send("/menu")
    assert labels(harness)[0] == "✂️ Записатися"
    await harness.send("/owner")
    await harness.send("/menu")
    assert labels(harness)[0] == "📊 Огляд бізнесу"
    harness.services.users.identify.return_value = Actor(
        harness.customer, frozenset({"CUSTOMER"}), None
    )
    await harness.send("/menu")
    assert labels(harness)[0] == "✂️ Записатися"


async def test_revoked_owner_cannot_continue_existing_form(harness):
    harness.services.users.identify.return_value = Actor(
        harness.customer, frozenset({"OWNER"}), None
    )
    await harness.send("/owner")
    await harness.callback("form", {"kind": "employee_create"})
    harness.services.users.identify.return_value = Actor(
        harness.customer, frozenset({"CUSTOMER"}), None
    )
    await harness.send("Чужий працівник")
    assert "не маєте дозволу" in harness.session.messages[-1].text
    harness.services.admin.create_employee.assert_not_awaited()


async def test_catalog_shows_current_names_prices_durations_and_descriptions(harness):
    from appointment_booking.app.database.models import Service

    harness.services.catalog.services.return_value = [
        Service(
            id=uuid4(),
            name={"uk": "Чоловіча стрижка"},
            description={"uk": "Стрижка та укладання."},
            duration_minutes=45,
            price_minor=45000,
            currency="UAH",
        )
    ]
    await harness.send("💈 Послуги та ціни")
    body = harness.session.messages[-1].text
    assert "Послуги та ціни ATELIER BARBER" in body
    assert "Чоловіча стрижка" in body
    assert "45 хв · 450 грн" in body
    assert "Стрижка та укладання." in body
    assert "Чоловіча стрижка · 450 грн" in labels(harness)
