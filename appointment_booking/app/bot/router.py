import logging
import secrets
from time import monotonic
from typing import Any
from uuid import UUID

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject
from pydantic import ValidationError

from appointment_booking.app.bot.dependencies import Services
from appointment_booking.app.bot.forms import Form
from appointment_booking.app.bot.handlers.customer import CustomerScreens
from appointment_booking.app.bot.handlers.management import ManagementScreens
from appointment_booking.app.bot.texts import text
from appointment_booking.app.bot.ui import Screen, safe
from appointment_booking.app.core.exceptions import BusinessError, SlotUnavailable, StaleSelection
from appointment_booking.app.core.security import FlowStore, RateLimiter
from appointment_booking.app.domain.dto import EmployeeInput

logger = logging.getLogger(__name__)


class ContextMiddleware(BaseMiddleware):
    def __init__(self, services: Services, flows: FlowStore, limiter: RateLimiter) -> None:
        self.services, self.flows, self.limiter = services, flows, limiter

    async def __call__(self, handler: Any, event: TelegramObject, data: dict[str, Any]) -> Any:
        if not isinstance(event, Message | CallbackQuery) or event.from_user is None:
            return None
        user = event.from_user
        message = event if isinstance(event, Message) else event.message
        if not isinstance(message, Message) or message.chat.type != "private":
            return None
        locale = "uk"
        started, correlation = monotonic(), secrets.token_hex(4)
        if isinstance(event, CallbackQuery):
            await event.answer()
        references = ""
        try:
            await self.limiter.check(user.id)
            actor = await self.services.users.identify(user.id, user.full_name, locale)
            locale = actor.user.locale
            data["screen"] = Screen(event, actor, self.flows)
            result = await handler(event, data)
            logger.info(
                "request",
                extra={
                    "operation": type(event).__name__,
                    "outcome": "success",
                    "duration_ms": round((monotonic() - started) * 1000),
                    "correlation_id": correlation,
                },
            )
            return result
        except BusinessError as exc:
            key, reference = "error_" + exc.code, ""
            references = ", ".join(getattr(exc, "references", ()))
        except (ValidationError, ValueError, KeyError):
            key, reference = "error_invalid", ""
        except Exception as exc:
            key, reference = "error_internal", correlation
            logger.error("request_failed reference=%s category=%s", reference, type(exc).__name__)
        # A failure during Redis access must still produce a recovery response.
        body = text(locale, key, reference=reference, references=references)
        screen = data.get("screen")
        if screen:
            try:
                await screen.show(body, [])
                return None
            except Exception:
                pass
        await message.answer(body + "\n/menu")
        return None


def build_router(services: Services, flows: FlowStore, limiter: RateLimiter) -> Router:
    router = Router(name="atelier")
    middleware = ContextMiddleware(services, flows, limiter)
    router.message.outer_middleware(middleware)
    router.callback_query.outer_middleware(middleware)
    customer, management = CustomerScreens(services), ManagementScreens(services)

    async def home(screen: Screen, data: dict[str, Any]) -> None:
        view = await flows.view(screen.actor.user.telegram_id, screen.actor.primary_role)
        if view == "OWNER" and screen.actor.admin:
            await management.dashboard(screen)
        elif view == "BARBER" and screen.actor.employee:
            await management.dashboard(screen, employee=True)
        else:
            await customer.home(screen, data)

    @router.message(Command("start", "menu", "cancel", "owner", "admin", "barber", "customer"))
    async def start(
        message: Message, screen: Screen, state: FSMContext, command: CommandObject
    ) -> None:
        await flows.advance(screen.actor.user.telegram_id)
        await state.clear()
        if command.command == "start" and command.args and command.args.startswith("join_"):
            await services.admin.activate_invitation(screen.actor.user.id, command.args[5:])
            await screen.show(screen.t("activated"), [], home=False)
        elif command.command in {"owner", "admin"}:
            screen.actor.require_admin()
            await flows.set_view(screen.actor.user.telegram_id, "OWNER")
            await management.dashboard(screen)
        elif command.command == "barber":
            screen.actor.require_barber()
            await flows.set_view(screen.actor.user.telegram_id, "BARBER")
            await management.dashboard(screen, employee=True)
        elif command.command == "customer":
            await flows.set_view(screen.actor.user.telegram_id, "CUSTOMER")
            await customer.home(screen, {})
        else:
            await home(screen, {})

    primary_actions = {
        text("uk", key): key
        for key in (
            "book",
            "mine",
            "catalog",
            "about",
            "contacts",
            "barber_schedule",
            "barber_today",
            "barber_upcoming",
            "barber_profile",
            "barber_settings",
            "stats",
            "employees",
            "services",
            "all_appointments",
            "hours",
            "closures",
            "settings",
        )
    }

    @router.message(F.text.in_(primary_actions))
    async def primary_navigation(message: Message, screen: Screen, state: FSMContext) -> None:
        await flows.advance(screen.actor.user.telegram_id)
        await state.clear()
        action = primary_actions[message.text or ""]
        if action in {"book", "catalog"}:
            await customer.catalog(screen, {"booking": action == "book"})
        elif action == "mine":
            await customer.appointments(screen, {})
        elif action in {"about", "contacts"}:
            await customer.info(screen, action)
        elif action in {"barber_today", "barber_upcoming"}:
            screen.actor.require_barber()
            await customer.appointments(
                screen,
                {"scope": "employee", **({"period": "today"} if action == "barber_today" else {})},
            )
        elif action.startswith("barber_"):
            await management.barber(screen, action)
        else:
            screen.actor.require_admin()
            if action == "stats":
                await management.overview(screen)
            elif action == "all_appointments":
                await customer.appointments(screen, {"scope": "admin"})
            elif action == "closures":
                await screen.show(
                    screen.t("closures"),
                    [
                        [await screen.button(screen.t(key), "manage", {"kind": key})]
                        for key in ("holidays", "blocks", "exceptions")
                    ],
                )
            else:
                await management.manage(screen, {"kind": action}, state)

    @router.callback_query(F.data.startswith("v1:"))
    async def callback(query: CallbackQuery, screen: Screen, state: FSMContext) -> None:
        try:
            action, data = await flows.get(query.from_user.id, query.data or "")
        except StaleSelection:
            # A committed confirmation remains recoverable after Redis TTL or restart.
            item = await services.bookings.replay(screen.actor.user.id, (query.data or "")[3:])
            await flows.advance(screen.actor.user.telegram_id)
            await customer.detail(screen, {"id": str(item.id)})
            return
        await flows.advance(screen.actor.user.telegram_id)
        if action == "employee_services":
            await management.service_selection(screen, state, data)
            return
        if action == "form_back":
            await management.back(screen, state, data)
            return
        if action == "form_apply":
            await management.apply(screen, state, data)
            return
        await state.clear()
        routes = {
            "home": home,
            "catalog": customer.catalog,
            "service": customer.service,
            "employees": customer.employees,
            "dates": customer.dates,
            "times": customer.times,
            "summary": customer.summary,
            "confirm": customer.confirm,
            "appointments": customer.appointments,
            "detail": customer.detail,
            "change_apply": customer.change_apply,
            "events": customer.events,
        }
        if action in routes:
            try:
                await routes[action](screen, data)
            except SlotUnavailable:
                if action != "confirm":
                    raise
                await customer.times(screen, {**data, "unavailable": True})
        elif action in {"barber_schedule", "barber_profile", "barber_settings"}:
            await management.barber(screen, action)
        elif action == "barber_date":
            screen.actor.require_barber()
            await management.start_form(screen, state, "barber_date")
        elif action == "time_off_form":
            await management.start_form(screen, state, "time_off")
        elif action == "time_off_list":
            await management.time_off(screen, data)
        elif action in {"time_off_review", "time_off_apply"}:
            screen.actor.require_admin()
            if action == "time_off_review":
                await screen.show(
                    screen.t("review"),
                    [
                        [await screen.button(screen.t("confirm"), "time_off_apply", data)],
                        [await screen.button(screen.t("cancel"), "time_off_list")],
                    ],
                )
            else:
                await services.admin.review_time_off(
                    screen.actor.user.id, UUID(data["id"]), data["approve"]
                )
                await management.time_off(screen, {})
        elif action == "appointment_filters":
            await management.appointment_filters(screen, data)
        elif action == "filter_choices":
            await management.filter_choices(screen, data)
        elif action == "owner_date":
            screen.actor.require_admin()
            await management.start_form(screen, state, "owner_date", metadata=data)
        elif action == "overview":
            await management.overview(screen)
        elif action == "closures":
            screen.actor.require_admin()
            await screen.show(
                screen.t("closures"),
                [
                    [await screen.button(screen.t(key), "manage", {"kind": key})]
                    for key in ("holidays", "blocks", "exceptions")
                ],
            )
        elif action == "invite":
            screen.actor.require_admin()
            token = await services.admin.invite(screen.actor.user.id, UUID(data["id"]))
            assert query.bot is not None
            bot_user = await query.bot.get_me()
            await screen.show(
                screen.t(
                    "invite_text",
                    name="барбера",
                    link=f"https://t.me/{bot_user.username}?start=join_{token}",
                ),
                [],
            )
        elif action == "deactivate_review":
            screen.actor.require_admin()
            await screen.show(
                screen.t("deactivate_review", name=safe(data["defaults"]["name"])),
                [
                    [await screen.button(screen.t("confirm"), "deactivate_apply", data)],
                    [await screen.button(screen.t("cancel"), "manage", {"kind": "employees"})],
                ],
            )
        elif action == "deactivate_apply":
            screen.actor.require_admin()
            await services.admin.save_employee(
                screen.actor.user.id,
                EmployeeInput.model_validate({**data["defaults"], "active": False}),
                UUID(data["id"]),
                data["revision"],
            )
            await management.manage(screen, {"kind": "employees"}, state)
        elif action == "book":
            await customer.catalog(screen, {**data, "booking": True})
        elif action == "mine":
            await customer.appointments(screen, {})
        elif action in {"about", "contacts"}:
            await customer.info(screen, action)
        elif action == "profile":
            await management.start_form(screen, state, "profile")
        elif action in {"admin", "employee"}:
            await management.dashboard(screen, employee=action == "employee")
        elif action == "manage":
            await management.manage(screen, data, state)
        elif action == "manage_detail":
            await management.detail(screen, data)
        elif action == "form":
            kind = data["kind"]
            if kind == "blocks":
                screen.actor.manage_blocks(
                    UUID(data["employee_id"]) if data.get("employee_id") else None
                )
            elif kind in {"hours", "exceptions"}:
                screen.actor.manage_availability(
                    UUID(data["employee_id"]) if data.get("employee_id") else None
                )
            else:
                screen.actor.require_admin()
            await management.start_form(screen, state, kind, data.get("defaults"), data)
        elif action in {"reschedule", "change_review"}:
            if screen.actor.admin:
                await management.start_form(
                    screen, state, "reason", metadata={"return_action": action, "return_data": data}
                )
            elif action == "reschedule":
                await customer.reschedule(screen, data)
            else:
                await customer.change_review(screen, data)
        elif action == "assignments":
            await management.assignments(screen, data)
        elif action in {"remove_review", "assignment_review", "retry_review"}:
            if action != "remove_review" or data["kind"] == "holidays":
                screen.actor.require_admin()
            elif data["kind"] == "blocks":
                screen.actor.manage_blocks(
                    UUID(data["employee_id"]) if data.get("employee_id") else None
                )
            else:
                screen.actor.manage_availability(
                    UUID(data["employee_id"]) if data.get("employee_id") else None
                )
            await screen.show(
                screen.t("review") + "\n" + safe(data.get("label", "")),
                [
                    [
                        await screen.button(
                            screen.t("confirm"), action.replace("_review", "_apply"), data
                        )
                    ]
                ],
            )
        elif action == "remove_apply":
            await services.admin.remove_special(
                screen.actor.user.id, data["kind"], UUID(data["id"])
            )
            await management.manage(
                screen, {"kind": data["kind"], "employee_id": data.get("employee_id")}, state
            )
        elif action == "assignment_apply":
            await services.admin.assign(
                screen.actor.user.id,
                UUID(data["employee_id"]),
                UUID(data["service_id"]),
                data["enabled"],
            )
            await management.assignments(screen, data)
        elif action == "retry_apply":
            await services.admin.retry_job(screen.actor.user.id, UUID(data["id"]))
            await management.manage(screen, {"kind": "jobs"}, state)
        elif action == "customers_page":
            await management.customers(screen, data)
        else:
            raise StaleSelection()

    @router.callback_query()
    async def outdated(query: CallbackQuery, screen: Screen) -> None:
        await screen.show(screen.t("error_stale"), [])

    @router.message(Form.input, F.text)
    async def form_input(message: Message, screen: Screen, state: FSMContext) -> None:
        await flows.advance(screen.actor.user.telegram_id)
        await management.input(screen, state, message.text or "")

    @router.message()
    async def fallback(message: Message, screen: Screen, state: FSMContext) -> None:
        await flows.advance(screen.actor.user.telegram_id)
        await state.clear()
        await home(screen, {})

    return router
