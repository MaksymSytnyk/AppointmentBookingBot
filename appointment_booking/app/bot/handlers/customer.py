import secrets
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from appointment_booking.app.bot.dependencies import Services
from appointment_booking.app.bot.ui import Screen, money, safe
from appointment_booking.app.core.clock import utcnow
from appointment_booking.app.domain.dto import BookCommand


class CustomerScreens:
    def __init__(self, services: Services) -> None:
        self.services = services

    async def home(self, screen: Screen, data: dict[str, Any]) -> None:
        settings = await self.services.catalog.settings(screen.actor.user.id)
        actions = ["book", "mine", "catalog", "about", "contacts"]
        rows = [[await screen.button(screen.t(key), key)] for key in actions]
        await screen.show(
            screen.t("menu", name=safe(settings.name)), rows, home=False, primary=True
        )

    async def info(self, screen: Screen, action: str) -> None:
        settings = await self.services.catalog.settings(screen.actor.user.id)
        await screen.show(safe(getattr(settings, action)), [])

    async def catalog(self, screen: Screen, data: dict[str, Any]) -> None:
        page = int(data.get("page", 0))
        services = await self.services.catalog.services(screen.actor.user.id, page)
        booking = data.get("booking", False)
        lines = [screen.t("choose_service") if booking else screen.t("catalog_intro")]
        rows = []
        for service in services:
            name = service.name.get("uk", "Послуга")
            currency = "грн" if service.currency == "UAH" else service.currency
            price = money(service.price_minor)
            lines.append(
                "\n"
                + safe(name)
                + "\n"
                + screen.t(
                    "duration_price",
                    minutes=service.duration_minutes,
                    price=price,
                    currency=currency,
                )
            )
            if not booking:
                lines.append(safe(service.description.get("uk", "")))
            rows.append(
                [
                    await screen.button(
                        f"{name} · {price} {currency}",
                        "service",
                        {"service_id": str(service.id), "booking": booking},
                    )
                ]
            )
        rows.append(
            await screen.pages("book" if booking else "catalog", {}, page, len(services) == 8)
        )
        if not services:
            lines.append(screen.t("catalog_empty"))
        await screen.show("\n".join(lines), rows)

    async def service(self, screen: Screen, data: dict[str, Any]) -> None:
        service = await self.services.catalog.service(
            screen.actor.user.id, UUID(data["service_id"])
        )
        body = "\n".join(
            [
                safe(service.name.get("uk", "Послуга")),
                safe(service.description.get("uk", "Послуга")),
                screen.t(
                    "duration_price",
                    minutes=service.duration_minutes,
                    price=money(service.price_minor),
                    currency="грн" if service.currency == "UAH" else service.currency,
                ),
            ]
        )
        await screen.show(
            body,
            [
                [await screen.button(screen.t("book"), "employees", data)],
                [
                    await screen.button(
                        screen.t("back"), "book" if data.get("booking") else "catalog"
                    )
                ],
            ],
        )

    async def employees(self, screen: Screen, data: dict[str, Any]) -> None:
        page = int(data.get("page", 0))
        employees = await self.services.catalog.employees(
            screen.actor.user.id, UUID(data["service_id"]), page
        )
        base = {k: v for k, v in data.items() if k not in {"page", "employee_id"}}
        rows = [[await screen.button(screen.t("any"), "dates", base)]]
        for employee in employees:
            rows.append(
                [
                    await screen.button(
                        employee.name, "dates", {**base, "employee_id": str(employee.id)}
                    )
                ]
            )
        rows.append(await screen.pages("employees", base, page, len(employees) == 8))
        rows.append([await screen.button(screen.t("back"), "service", base)])
        body = screen.t("choose_employee")
        if any(employee.is_demo for employee in employees):
            body += "\n\n" + screen.t("demo_barber_notice")
        await screen.show(body, rows)

    async def dates(self, screen: Screen, data: dict[str, Any]) -> None:
        settings = await self.services.catalog.settings(screen.actor.user.id)
        today = utcnow().astimezone(ZoneInfo(settings.timezone)).date()
        page = max(0, min(int(data.get("page", 0)), settings.horizon_days // 7))
        base = {k: v for k, v in data.items() if k not in {"page", "day"}}
        rows = []
        for offset in range(page * 7, min(page * 7 + 7, settings.horizon_days + 1)):
            day = today + timedelta(days=offset)
            slots = await self.services.availability.slots(
                screen.actor.user.id,
                UUID(data["service_id"]),
                day,
                UUID(data["employee_id"]) if data.get("employee_id") else None,
                UUID(data["appointment_id"]) if data.get("appointment_id") else None,
            )
            if slots:
                rows.append(
                    [
                        await screen.button(
                            day.isoformat(), "times", {**base, "day": day.isoformat()}
                        )
                    ]
                )
        rows.append(
            await screen.pages("dates", base, page, (page + 1) * 7 <= settings.horizon_days)
        )
        rows.append([await screen.button(screen.t("back"), "employees", base)])
        await screen.show(
            screen.t("choose_date") + ("\n" + screen.t("empty") if not any(rows[:-2]) else ""), rows
        )

    async def times(self, screen: Screen, data: dict[str, Any]) -> None:
        settings = await self.services.catalog.settings(screen.actor.user.id)
        zone = ZoneInfo(settings.timezone)
        slots = await self.services.availability.slots(
            screen.actor.user.id,
            UUID(data["service_id"]),
            date.fromisoformat(data["day"]),
            UUID(data["employee_id"]) if data.get("employee_id") else None,
            UUID(data["appointment_id"]) if data.get("appointment_id") else None,
        )
        # For Any, present one named employee per start; confirmation fixes that employee.
        unique = {slot.starts_at: slot for slot in reversed(slots)}
        slots = sorted(unique.values(), key=lambda s: s.starts_at)
        page = max(0, int(data.get("page", 0)))
        base = {k: v for k, v in data.items() if k not in {"page", "unavailable"}}
        rows = []
        for slot in slots[page * 12 : (page + 1) * 12]:
            label = (
                slot.starts_at.astimezone(zone).strftime("%H:%M %z") + " · " + slot.employee_name
            )
            rows.append(
                [
                    await screen.button(
                        label,
                        "summary",
                        {
                            **base,
                            "employee_id": str(slot.employee_id),
                            "employee_name": slot.employee_name,
                            "starts_at": slot.starts_at.isoformat(),
                        },
                    )
                ]
            )
        rows.append(await screen.pages("times", base, page, len(slots) > (page + 1) * 12))
        rows.append([await screen.button(screen.t("back"), "dates", base)])
        await screen.show(
            (screen.t("error_unavailable") + "\n\n" if data.get("unavailable") else "")
            + screen.t("choose_time", zone=settings.timezone)
            + ("\n" + screen.t("no_slots") if not slots else ""),
            rows,
        )

    async def summary(self, screen: Screen, data: dict[str, Any]) -> None:
        user_id = screen.actor.user.id
        service = await self.services.catalog.service(user_id, UUID(data["service_id"]))
        settings = await self.services.catalog.settings(user_id)
        start = datetime.fromisoformat(data["starts_at"])
        zone = ZoneInfo(settings.timezone)
        duration, price, currency = service.duration_minutes, service.price_minor, service.currency
        if data.get("appointment_id"):
            appointment, _, _ = await self.services.bookings.detail(
                user_id, UUID(data["appointment_id"])
            )
            duration, price, currency = (
                appointment.duration_minutes,
                appointment.price_minor,
                appointment.currency,
            )
        end = start + timedelta(minutes=duration)
        body = "\n".join(
            [
                screen.t("review"),
                safe(service.name.get("uk", "Послуга")),
                safe(data["employee_name"]),
                start.astimezone(zone).strftime("%Y-%m-%d %H:%M %z")
                + " – "
                + end.astimezone(zone).strftime("%H:%M %z"),
                screen.t(
                    "duration_price",
                    minutes=duration,
                    price=money(price),
                    currency="грн" if currency == "UAH" else currency,
                ),
                screen.t("policy_text", hours=settings.cancellation_hours),
            ]
        )
        token = secrets.token_urlsafe(16)
        payload = {
            **data,
            "request_token": token,
            "service_revision": service.revision,
            "settings_revision": settings.revision,
        }
        await screen.show(
            body,
            [
                [await screen.button(screen.t("confirm"), "confirm", payload, token)],
                [await screen.button(screen.t("back"), "times", data)],
            ],
        )

    async def confirm(self, screen: Screen, data: dict[str, Any]) -> None:
        if data.get("appointment_id"):
            appointment = await self.services.bookings.change(
                screen.actor.user.id,
                UUID(data["appointment_id"]),
                version=data["version"],
                action="rescheduled",
                reason=data.get("reason", ""),
                starts_at=datetime.fromisoformat(data["starts_at"]),
                employee_id=UUID(data["employee_id"]),
            )
        else:
            command = BookCommand.model_validate(
                {key: data[key] for key in BookCommand.model_fields}
            )
            appointment = await self.services.bookings.book(screen.actor.user.id, command)
        _, barber, customer = await self.services.bookings.detail(
            screen.actor.user.id, appointment.id
        )
        settings = await self.services.catalog.settings(screen.actor.user.id)
        local = appointment.starts_at.astimezone(ZoneInfo(settings.timezone))
        await screen.show(
            screen.t(
                "booked",
                reference=appointment.reference,
                customer=safe(customer),
                service=safe(appointment.service_label.get("uk", "Послуга")),
                barber=safe(barber),
                day=local.strftime("%d.%m.%Y"),
                time=local.strftime("%H:%M"),
            ),
            [[await screen.button(screen.t("details"), "detail", {"id": str(appointment.id)})]],
        )

    async def appointments(self, screen: Screen, data: dict[str, Any]) -> None:
        page, scope = int(data.get("page", 0)), data.get("scope", "mine")
        start, end = None, None
        settings = await self.services.catalog.settings(screen.actor.user.id)
        zone = ZoneInfo(settings.timezone)
        if data.get("day"):
            day = date.fromisoformat(data["day"])
            start = datetime.combine(day, datetime.min.time(), zone).astimezone(UTC)
            end = datetime.combine(day + timedelta(days=1), datetime.min.time(), zone).astimezone(
                UTC
            )
        elif data.get("period") in {"today", "week"}:
            today = utcnow().astimezone(zone).date()
            start = datetime.combine(today, datetime.min.time(), zone).astimezone(UTC)
            days = 1 if data["period"] == "today" else 7
            end = datetime.combine(
                today + timedelta(days=days), datetime.min.time(), zone
            ).astimezone(UTC)
        elif not data.get("history") and (scope == "employee" or data.get("upcoming")):
            start = utcnow()
        items = await self.services.bookings.list(
            screen.actor.user.id,
            scope=scope,
            page=page,
            filter_employee_id=UUID(data["employee_id"]) if data.get("employee_id") else None,
            service_id=UUID(data["service_id"]) if data.get("service_id") else None,
            status=data.get("status"),
            history=bool(data.get("history")),
            start=start,
            end=end,
        )
        rows = [
            [
                await screen.button(
                    a.starts_at.astimezone(zone).strftime("%d.%m %H:%M")
                    + " · "
                    + screen.t(a.status)
                    + " · "
                    + a.reference,
                    "detail",
                    {"id": str(a.id)},
                )
            ]
            for a in items
        ]
        rows.append(await screen.pages("appointments", data, page, len(items) == 8))
        if scope == "admin":
            rows.append([await screen.button(screen.t("filters"), "appointment_filters", data)])
        if scope == "mine":
            rows.append(
                [
                    await screen.button(screen.t("upcoming"), "mine"),
                    await screen.button(screen.t("history"), "appointments", {"history": True}),
                ]
            )
        else:
            rows.append(
                [
                    await screen.button(
                        screen.t("today"),
                        "appointments",
                        {**data, "page": 0, "scope": scope, "period": "today", "day": None},
                    ),
                    await screen.button(
                        screen.t("week"),
                        "appointments",
                        {**data, "page": 0, "scope": scope, "period": "week", "day": None},
                    ),
                ]
            )
        await screen.show(
            screen.t("mine" if scope == "mine" else "upcoming")
            + ("\n" + screen.t("empty") if not items else ""),
            rows,
        )

    async def detail(self, screen: Screen, data: dict[str, Any]) -> None:
        item, employee, customer = await self.services.bookings.detail(
            screen.actor.user.id, UUID(data["id"])
        )
        settings = await self.services.catalog.settings(screen.actor.user.id)
        zone = ZoneInfo(settings.timezone)
        body = "\n".join(
            [
                safe(item.reference),
                safe(item.service_label.get("uk", "Послуга")),
                safe(employee),
                screen.t("customer") + ": " + safe(customer),
                item.starts_at.astimezone(zone).strftime("%Y-%m-%d %H:%M %z")
                + " – "
                + item.ends_at.astimezone(zone).strftime("%H:%M %z"),
                screen.t(item.status),
                f"{money(item.price_minor)} {'грн' if item.currency == 'UAH' else item.currency}",
            ]
        )
        rows = []
        base = {"id": str(item.id), "version": item.version}
        if item.status == "confirmed":
            if screen.actor.admin or item.customer_id == screen.actor.user.id:
                rows.append(
                    [
                        await screen.button(
                            screen.t("cancel_booking"),
                            "change_review",
                            {**base, "action": "cancelled"},
                        ),
                        await screen.button(screen.t("reschedule"), "reschedule", base),
                    ]
                )
            if screen.actor.admin or (
                screen.actor.employee and screen.actor.employee.id == item.employee_id
            ):
                rows.append(
                    [
                        await screen.button(
                            screen.t("completed"), "change_review", {**base, "action": "completed"}
                        ),
                        await screen.button(
                            screen.t("no_show"), "change_review", {**base, "action": "no_show"}
                        ),
                    ]
                )
        if (
            item.status == "confirmed"
            and screen.actor.employee
            and screen.actor.employee.id == item.employee_id
            and screen.actor.employee.may_cancel_appointments
            and not screen.actor.admin
            and item.customer_id != screen.actor.user.id
        ):
            rows.append(
                [
                    await screen.button(
                        screen.t("cancel_booking"), "change_review", {**base, "action": "cancelled"}
                    )
                ]
            )
        rows.append([await screen.button(screen.t("history"), "events", {"id": str(item.id)})])
        await screen.show(body, rows)

    async def change_review(self, screen: Screen, data: dict[str, Any]) -> None:
        await self.services.bookings.detail(screen.actor.user.id, UUID(data["id"]))
        await screen.show(
            screen.t("review") + "\n" + screen.t(data["action"]),
            [
                [await screen.button(screen.t("confirm"), "change_apply", data)],
                [await screen.button(screen.t("back"), "detail", data)],
            ],
        )

    async def change_apply(self, screen: Screen, data: dict[str, Any]) -> None:
        await self.services.bookings.change(
            screen.actor.user.id,
            UUID(data["id"]),
            version=int(data["version"]),
            action=data["action"],
            reason=data.get("reason", ""),
        )
        await self.detail(screen, data)

    async def reschedule(self, screen: Screen, data: dict[str, Any]) -> None:
        item, _, _ = await self.services.bookings.detail(screen.actor.user.id, UUID(data["id"]))
        await self.employees(
            screen,
            {
                "service_id": str(item.service_id),
                "appointment_id": str(item.id),
                "version": item.version,
                "reason": data.get("reason", ""),
            },
        )

    async def events(self, screen: Screen, data: dict[str, Any]) -> None:
        page = int(data.get("page", 0))
        events = await self.services.bookings.events(screen.actor.user.id, UUID(data["id"]), page)
        body = "\n".join(
            safe(e.created_at.isoformat()) + " · " + screen.t(e.action) for e in events
        )
        await screen.show(
            body or screen.t("empty"),
            [
                await screen.pages("events", data, page, len(events) == 8),
                [await screen.button(screen.t("back"), "detail", data)],
            ],
        )
