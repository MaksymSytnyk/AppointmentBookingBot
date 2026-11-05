from datetime import date
from typing import Any
from uuid import UUID

from aiogram.fsm.context import FSMContext

from appointment_booking.app.bot.dependencies import Services
from appointment_booking.app.bot.forms import FIELDS, Form, parse_field
from appointment_booking.app.bot.ui import Screen, safe
from appointment_booking.app.core.exceptions import PermissionDenied, StaleSelection
from appointment_booking.app.domain.dto import (
    BlockInput,
    EmployeeInput,
    ExceptionInput,
    HoursInput,
    ProfileInput,
    ServiceInput,
    SettingsInput,
)


def display_value(screen: Screen, value: Any) -> str:
    if isinstance(value, bool):
        return screen.t("yes" if value else "no")
    return safe("—" if value is None else value)


def entity_data(item: Any, fields: list[str]) -> dict[str, Any]:
    result = {}
    for field in fields:
        if field.startswith(("name_", "description_")):
            column, locale = field.rsplit("_", 1)
            value = getattr(item, column)[locale]
        else:
            value = getattr(item, field, None)
        result[field] = str(value) if isinstance(value, UUID) else value
    return result


class ManagementScreens:
    def __init__(self, services: Services) -> None:
        self.services = services

    async def dashboard(self, screen: Screen, *, employee: bool = False) -> None:
        if employee:
            screen.actor.require_barber()
            assert screen.actor.employee is not None
            rows = [
                [await screen.button(screen.t("barber_schedule"), "barber_schedule")],
                [
                    await screen.button(
                        screen.t("barber_today"),
                        "appointments",
                        {"scope": "employee", "period": "today"},
                    )
                ],
                [
                    await screen.button(
                        screen.t("barber_upcoming"), "appointments", {"scope": "employee"}
                    )
                ],
                [await screen.button(screen.t("barber_profile"), "barber_profile")],
                [await screen.button(screen.t("barber_settings"), "barber_settings")],
            ]
            await screen.show(
                screen.t("employee", name=safe(screen.actor.employee.name)),
                rows,
                home=False,
                primary=True,
            )
        else:
            screen.actor.require_admin()
            rows = [
                [await screen.button(screen.t("stats"), "overview")],
                [await screen.button(screen.t("employees"), "manage", {"kind": "employees"})],
                [await screen.button(screen.t("services"), "manage", {"kind": "services"})],
                [
                    await screen.button(
                        screen.t("all_appointments"), "appointments", {"scope": "admin"}
                    )
                ],
                [await screen.button(screen.t("hours"), "manage", {"kind": "hours"})],
                [await screen.button(screen.t("closures"), "closures")],
                [await screen.button(screen.t("settings"), "manage", {"kind": "settings"})],
            ]
            await screen.show(screen.t("admin"), rows, home=False, primary=True)

    async def overview(self, screen: Screen) -> None:
        from datetime import timedelta
        from zoneinfo import ZoneInfo

        from appointment_booking.app.core.clock import utcnow

        screen.actor.require_admin()
        settings = await self.services.catalog.settings(screen.actor.user.id)
        today = utcnow().astimezone(ZoneInfo(settings.timezone)).date()
        first = today - timedelta(days=today.weekday())
        stats = await self.services.admin.statistics(
            screen.actor.user.id, first, first + timedelta(days=6)
        )
        daily = await self.services.admin.statistics(screen.actor.user.id, today, today)
        lines = [
            screen.t("admin"),
            "",
            f"{screen.t('today')}: {daily['total']}",
            f"{screen.t('week_total')}: {stats['total']}",
        ]
        lines.extend(
            f"{screen.t(key)}: {stats['counts'].get(key, 0)}"
            for key in ("completed", "cancelled", "no_show")
        )
        lines.extend(["", screen.t("popular")])
        lines.extend(f"{safe(n.get('uk', 'Послуга'))}: {count}" for n, count in stats["popular"])
        lines.extend(["", screen.t("workload")])
        lines.extend(
            f"{safe(name)}: {count} / {minutes}" for name, count, minutes in stats["workload"]
        )
        await screen.show(
            "\n".join(lines),
            [
                [await screen.button(screen.t("stats"), "manage", {"kind": "stats"})],
                [await screen.button(screen.t("customers"), "manage", {"kind": "customers"})],
                [await screen.button(screen.t("jobs"), "manage", {"kind": "jobs"})],
                [await screen.button(screen.t("time_off_queue"), "time_off_list")],
            ],
        )

    async def barber(self, screen: Screen, action: str) -> None:
        screen.actor.require_barber()
        employee, services, hours = await self.services.admin.barber_profile(screen.actor.user.id)
        if action == "barber_profile":
            weekdays = ("Понеділок", "Вівторок", "Середа", "Четвер", "П’ятниця", "Субота", "Неділя")
            lines = [
                f"{weekdays[day]}: "
                + (
                    ", ".join(
                        f"{h.opens:%H:%M}–{h.closes:%H:%M}" if h.opens else screen.t("closed")
                        for h in values
                    )
                    or screen.t("closed")
                )
                for day, values in enumerate(hours)
            ]
            await screen.show(
                screen.t(
                    "barber_profile_text",
                    name=safe(employee.name),
                    bio=safe(employee.bio or "—"),
                    services="\n".join(safe(item.name.get("uk", "Послуга")) for item in services)
                    or "—",
                    hours="\n".join(lines),
                ),
                [],
            )
        elif action == "barber_schedule":
            await screen.show(
                screen.t("barber_schedule"),
                [
                    [
                        await screen.button(
                            screen.t("today"),
                            "appointments",
                            {"scope": "employee", "period": "today"},
                        )
                    ],
                    [
                        await screen.button(
                            screen.t("week"),
                            "appointments",
                            {"scope": "employee", "period": "week"},
                        )
                    ],
                    [await screen.button(screen.t("selected_date"), "barber_date")],
                ],
            )
        else:
            rows = []
            if employee.may_manage_availability:
                rows = [
                    [
                        await screen.button(
                            screen.t(key), "manage", {"kind": key, "employee_id": str(employee.id)}
                        )
                    ]
                    for key in ("hours", "exceptions")
                ]
            if employee.may_block_periods:
                rows.append(
                    [
                        await screen.button(
                            screen.t("blocks"),
                            "manage",
                            {"kind": "blocks", "employee_id": str(employee.id)},
                        )
                    ]
                )
            if employee.may_request_time_off:
                rows.append([await screen.button(screen.t("request_time_off"), "time_off_form")])
            rows.append([await screen.button(screen.t("my_time_off"), "time_off_list")])
            await screen.show(
                screen.t(
                    "barber_settings_text",
                    availability=screen.t("yes" if employee.may_manage_availability else "no"),
                    cancellation=screen.t("yes" if employee.may_cancel_appointments else "no"),
                    blocks=screen.t("yes" if employee.may_block_periods else "no"),
                    requests=screen.t("yes" if employee.may_request_time_off else "no"),
                ),
                rows,
            )

    async def appointment_filters(self, screen: Screen, data: dict[str, Any]) -> None:
        screen.actor.require_admin()
        rows = [[await screen.button(screen.t("selected_date"), "owner_date", data)]]
        for key in ("employees", "services", "status"):
            rows.append(
                [
                    await screen.button(
                        screen.t(key), "filter_choices", {**data, "filter_kind": key, "page": 0}
                    )
                ]
            )
        rows.append(
            [await screen.button(screen.t("reset_filters"), "appointments", {"scope": "admin"})]
        )
        await screen.show(screen.t("filters"), rows)

    async def filter_choices(self, screen: Screen, data: dict[str, Any]) -> None:
        screen.actor.require_admin()
        kind, page = data["filter_kind"], int(data.get("page", 0))
        base = {k: v for k, v in data.items() if k not in {"filter_kind", "page"}}
        rows = []
        if kind == "status":
            rows = [
                [await screen.button(screen.t(status), "appointments", {**base, "status": status})]
                for status in (
                    "confirmed",
                    "completed",
                    "cancelled",
                    "no_show",
                    "pending",
                    "expired",
                )
            ]
        else:
            items: list[Any] = await (
                self.services.admin.employees(screen.actor.user.id, page)
                if kind == "employees"
                else self.services.admin.services(screen.actor.user.id, page)
            )
            for item in items:
                label = item.name.get("uk", "Послуга") if isinstance(item.name, dict) else item.name
                field = "employee_id" if kind == "employees" else "service_id"
                rows.append(
                    [await screen.button(label, "appointments", {**base, field: str(item.id)})]
                )
            rows.append(await screen.pages("filter_choices", data, page, len(items) == 8))
        await screen.show(screen.t("filters"), rows)

    async def manage(self, screen: Screen, data: dict[str, Any], state: FSMContext) -> None:
        kind, page, user_id = data["kind"], int(data.get("page", 0)), screen.actor.user.id
        employee_id = UUID(data["employee_id"]) if data.get("employee_id") else None
        if kind in {"customers", "stats", "settings"}:
            screen.actor.require_admin()
            defaults = {}
            metadata = {}
            if kind == "settings":
                settings_item = await self.services.catalog.settings(user_id)
                defaults = entity_data(settings_item, FIELDS["settings"])
                metadata["revision"] = settings_item.revision
            await self.start_form(screen, state, kind, defaults, metadata)
            return
        if kind == "hours":
            screen.actor.manage_availability(employee_id)
            rows = []
            lines = [screen.t("hours")]
            for day in range(7):
                hours = await self.services.admin.read_hours(user_id, employee_id, day)
                display = ", ".join(
                    f"{h.opens}–{h.closes}" if h.opens else screen.t("closed") for h in hours
                )
                lines.append(f"{screen.t('day_' + str(day))}: {display or '—'}")
            rows.append([await screen.button(screen.t("edit"), "form", {"kind": "hours", **data})])
            if screen.actor.admin and employee_id is None:
                rows.append(
                    [await screen.button(screen.t("employees"), "manage", {"kind": "employees"})]
                )
            await screen.show("\n".join(lines), rows)
            return
        rows = []
        items: list[Any]
        if kind in {"employees", "services"}:
            screen.actor.require_admin()
            items = await (
                self.services.admin.employees(user_id, page)
                if kind == "employees"
                else self.services.admin.services(user_id, page)
            )
            for item in items:
                label = item.name.get("uk", "Послуга") if isinstance(item.name, dict) else item.name
                rows.append(
                    [
                        await screen.button(
                            label, "manage_detail", {"kind": kind, "id": str(item.id), "page": page}
                        )
                    ]
                )
            rows.append(
                [
                    await screen.button(
                        screen.t("add"),
                        "form",
                        {"kind": kind[:-1] if kind == "services" else "employee_create"},
                    )
                ]
            )
        elif kind == "jobs":
            counts, items = await self.services.admin.jobs(user_id, page)
            for item in items:
                rows.append(
                    [
                        await screen.button(
                            screen.t("retry")
                            + " · "
                            + screen.t(
                                "notice_"
                                + ("reminder" if item.kind.startswith("reminder_") else item.kind)
                            ),
                            "retry_review",
                            {"id": str(item.id)},
                        )
                    ]
                )
            body = "\n".join(f"{screen.t(key)}: {value}" for key, value in counts.items())
            rows.append(await screen.pages("manage", data, page, len(items) == 8))
            await screen.show(body or screen.t("empty"), rows)
            return
        else:
            items = await self.services.admin.special(user_id, kind, page, employee_id)
            for item in items:
                label = getattr(item, "label", None) or str(
                    getattr(item, "day", None) or item.starts_at
                )
                rows.append(
                    [
                        await screen.button(
                            label,
                            "remove_review",
                            {
                                "kind": kind,
                                "id": str(item.id),
                                "label": label,
                                "employee_id": str(employee_id) if employee_id else None,
                            },
                        )
                    ]
                )
            rows.append([await screen.button(screen.t("add"), "form", data)])
        rows.append(await screen.pages("manage", data, page, len(items) == 8))
        await screen.show(screen.t(kind) + ("\n" + screen.t("empty") if not items else ""), rows)

    async def detail(self, screen: Screen, data: dict[str, Any]) -> None:
        screen.actor.require_admin()
        kind = data["kind"]
        page = int(data.get("page", 0))
        items: list[Any] = await (
            self.services.admin.employees(screen.actor.user.id, page)
            if kind == "employees"
            else self.services.admin.services(screen.actor.user.id, page)
        )
        item = next((i for i in items if str(i.id) == data["id"]), None)
        if item is None:
            raise StaleSelection()
        form_kind = "employee" if kind == "employees" else "service"
        defaults = entity_data(item, FIELDS[form_kind])
        lines: list[str] = []
        lines.extend(
            f"{screen.t('field_' + k)}: {display_value(screen, v)}" for k, v in defaults.items()
        )
        if kind == "employees" and item.is_demo:
            lines.append(screen.t("demo_barber_notice"))
        if kind == "employees" and not item.user_id:
            lines.append(screen.t("unlinked"))
        rows = [
            [
                await screen.button(
                    screen.t("edit"),
                    "form",
                    {
                        "kind": form_kind,
                        "id": str(item.id),
                        "revision": item.revision,
                        "defaults": defaults,
                    },
                )
            ]
        ]
        if kind == "employees":
            if not item.user_id and item.active and not item.is_demo:
                rows.append(
                    [await screen.button(screen.t("invite"), "invite", {"id": str(item.id)})]
                )
            if item.active:
                rows.append(
                    [
                        await screen.button(
                            screen.t("deactivate"),
                            "deactivate_review",
                            {"id": str(item.id), "revision": item.revision, "defaults": defaults},
                        )
                    ]
                )
            rows.append(
                [
                    await screen.button(
                        screen.t("upcoming"),
                        "appointments",
                        {"scope": "admin", "employee_id": str(item.id), "upcoming": True},
                    )
                ]
            )
            for section in ("hours", "blocks", "exceptions"):
                rows.append(
                    [
                        await screen.button(
                            screen.t(section),
                            "manage",
                            {"kind": section, "employee_id": str(item.id)},
                        )
                    ]
                )
            rows.append(
                [
                    await screen.button(
                        screen.t("assignments"), "assignments", {"employee_id": str(item.id)}
                    )
                ]
            )
        await screen.show("\n".join(lines), rows)

    async def assignments(self, screen: Screen, data: dict[str, Any]) -> None:
        screen.actor.require_admin()
        page, employee_id = int(data.get("page", 0)), UUID(data["employee_id"])
        assigned = await self.services.admin.assignments(screen.actor.user.id, employee_id)
        services = await self.services.admin.services(screen.actor.user.id, page)
        rows = [
            [
                await screen.button(
                    ("✓ " if s.id in assigned else "○ ") + s.name.get("uk", "Послуга"),
                    "assignment_review",
                    {
                        "employee_id": str(employee_id),
                        "service_id": str(s.id),
                        "enabled": s.id not in assigned,
                    },
                )
            ]
            for s in services
        ]
        rows.append(await screen.pages("assignments", data, page, len(services) == 8))
        await screen.show(screen.t("assignments"), rows)

    def authorize_form(self, screen: Screen, kind: str, metadata: dict[str, Any]) -> None:
        if kind == "profile":
            return
        if kind in {"barber_date", "time_off"}:
            screen.actor.require_barber()
            if kind == "time_off" and (
                screen.actor.employee is None or not screen.actor.employee.may_request_time_off
            ):
                raise PermissionDenied()
        elif kind == "blocks":
            screen.actor.manage_blocks(
                UUID(metadata["employee_id"]) if metadata.get("employee_id") else None
            )
        elif kind in {"hours", "exceptions"}:
            screen.actor.manage_availability(
                UUID(metadata["employee_id"]) if metadata.get("employee_id") else None
            )
        else:
            screen.actor.require_admin()

    async def start_form(
        self,
        screen: Screen,
        state: FSMContext,
        kind: str,
        defaults: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if kind not in FIELDS:
            raise StaleSelection()
        metadata = metadata or {}
        self.authorize_form(screen, kind, metadata)
        fields = list(FIELDS[kind])
        values = {}
        if "employee_id" in fields and metadata.get("employee_id"):
            fields.remove("employee_id")
            values["employee_id"] = metadata["employee_id"]
        if kind == "barber_date":
            screen.actor.require_barber()
        if kind == "profile":
            defaults = entity_data(screen.actor.user, fields)
        import secrets

        await state.set_state(Form.input)
        await state.set_data(
            {
                "form_id": secrets.token_urlsafe(16),
                "kind": kind,
                "fields": fields,
                "index": 0,
                "values": values,
                "defaults": defaults or {},
                "metadata": metadata,
            }
        )
        await self.prompt(screen, state)

    async def prompt(self, screen: Screen, state: FSMContext, *, invalid: bool = False) -> None:
        data = await state.get_data()
        self.authorize_form(screen, data["kind"], data["metadata"])
        field = data["fields"][data["index"]]
        if field == "service_ids":
            await self.service_selection(screen, state, {})
            return
        hint = screen.t("field_" + field)
        if invalid:
            hint = screen.t("error_invalid") + "\n" + hint
        current_values = data["defaults"] | data["values"]
        if field in current_values:
            current = current_values[field]
            hint += "\n" + display_value(screen, current) + "\n. = ✓"
        rows = [[await screen.button(screen.t("cancel"), "home")]]
        if data["index"]:
            rows.insert(
                0,
                [await screen.button(screen.t("back"), "form_back", {"form_id": data["form_id"]})],
            )
        await screen.show(hint + "\n" + screen.t("form_cancel"), rows, home=False)

    async def service_selection(
        self, screen: Screen, state: FSMContext, payload: dict[str, Any]
    ) -> None:
        screen.actor.require_admin()
        data = await state.get_data()
        if (
            data.get("kind") != "employee_create"
            or not data.get("fields")
            or data["fields"][data["index"]] != "service_ids"
        ):
            raise StaleSelection()
        selected = data["values"].get("service_ids", [])
        if payload.get("id"):
            identifier = payload["id"]
            selected = (
                [i for i in selected if i != identifier]
                if identifier in selected
                else [*selected, identifier]
            )
        if payload.get("done"):
            if not selected:
                raise StaleSelection()
            await self.input(screen, state, ",".join(selected))
            return
        data["values"]["service_ids"] = selected
        await state.set_data(data)
        page = int(payload.get("page", 0))
        services = await self.services.admin.services(screen.actor.user.id, page)
        labels = data.get("service_labels", {})
        labels.update({str(item.id): item.name.get("uk", "Послуга") for item in services})
        await state.update_data(service_labels=labels)
        rows = [
            [
                await screen.button(
                    ("✓ " if str(item.id) in selected else "○ ") + item.name.get("uk", "Послуга"),
                    "employee_services",
                    {"id": str(item.id), "page": page},
                )
            ]
            for item in services
            if item.active
        ]
        rows.append(await screen.pages("employee_services", {}, page, len(services) == 8))
        rows.append([await screen.button(screen.t("next"), "employee_services", {"done": True})])
        rows.append(
            [await screen.button(screen.t("back"), "form_back", {"form_id": data["form_id"]})]
        )
        rows.append([await screen.button(screen.t("cancel"), "home")])
        await screen.show(screen.t("field_service_ids"), rows, home=False)

    async def input(self, screen: Screen, state: FSMContext, value: str) -> None:
        data = await state.get_data()
        self.authorize_form(screen, data["kind"], data["metadata"])
        field = data["fields"][data["index"]]
        try:
            current_values = data["defaults"] | data["values"]
            parsed = (
                current_values[field]
                if value.strip() == "." and field in current_values
                else parse_field(field, value)
            )
        except (ValueError, KeyError):
            await self.prompt(screen, state, invalid=True)
            return
        data["values"][field] = parsed
        data["index"] += 1
        if data["index"] < len(data["fields"]):
            await state.set_data(data)
            await self.prompt(screen, state)
        else:
            # Typed validation occurs before a confirmation screen, then again in the service.
            self.validate_form(data["kind"], data["values"])
            import secrets

            data["nonce"] = secrets.token_urlsafe(16)
            await state.set_data(data)
            await state.set_state(Form.review)
            lines = [screen.t("form_review")]
            for key, val in data["values"].items():
                if key == "service_ids":
                    val = ", ".join(data.get("service_labels", {}).get(i, "Послуга") for i in val)
                elif key == "weekdays":
                    val = ", ".join(str(day + 1) for day in val)
                lines.append(f"{screen.t('field_' + key)}: {display_value(screen, val)}")
            await screen.show(
                "\n".join(lines),
                [
                    [
                        await screen.button(
                            screen.t("confirm"), "form_apply", {"nonce": data["nonce"]}
                        )
                    ],
                    [
                        await screen.button(
                            screen.t("back"), "form_back", {"form_id": data["form_id"]}
                        )
                    ],
                    [await screen.button(screen.t("cancel"), "home")],
                ],
            )

    async def back(self, screen: Screen, state: FSMContext, payload: dict[str, Any]) -> None:
        data = await state.get_data()
        if data.get("form_id") != payload.get("form_id") or data.get("index", 0) <= 0:
            raise StaleSelection()
        data["index"] -= 1
        await state.set_data(data)
        await state.set_state(Form.input)
        await self.prompt(screen, state)

    def validate_form(self, kind: str, values: dict[str, Any]) -> Any:
        if kind == "employee_create":
            from datetime import time

            from appointment_booking.app.domain.availability import validate_hours

            if (
                not values.get("service_ids")
                or not values.get("weekdays")
                or not values.get("intervals")
            ):
                raise ValueError("Employee configuration is incomplete")
            intervals = [
                (time.fromisoformat(a), time.fromisoformat(b)) for a, b in values["intervals"]
            ]
            validate_hours(intervals)
            return EmployeeInput.model_validate({key: values[key] for key in FIELDS["employee"]})
        if kind == "time_off":
            return BlockInput.model_validate(values)
        if kind == "service":
            values = dict(values)
            for key in ("name", "description"):
                values[key] = {lang: values.pop(key + "_" + lang) for lang in ("uk",)}
            return ServiceInput.model_validate(values)
        if kind == "hours":
            values = dict(values)
            intervals = values["intervals"]
            values["intervals"] = [] if isinstance(intervals, str) else intervals
            values["inherit"] = intervals == "inherit"
            return HoursInput.model_validate(values)
        if kind == "exceptions":
            values = dict(values)
            values["intervals"] = values.pop("exception_intervals")
            return ExceptionInput.model_validate(values)
        model: Any = {
            "employee": EmployeeInput,
            "settings": SettingsInput,
            "blocks": BlockInput,
            "profile": ProfileInput,
        }.get(kind)
        return model.model_validate(values) if model else values

    async def apply(self, screen: Screen, state: FSMContext, payload: dict[str, Any]) -> None:
        data = await state.get_data()
        if await state.get_state() != Form.review.state or data.get("nonce") != payload.get(
            "nonce"
        ):
            raise StaleSelection()
        kind, values, metadata = data["kind"], data["values"], data["metadata"]
        self.authorize_form(screen, kind, metadata)
        parsed = self.validate_form(kind, values)
        user_id = screen.actor.user.id
        identifier = UUID(metadata["id"]) if metadata.get("id") else None
        if kind in {"barber_date", "owner_date"}:
            if kind == "barber_date":
                screen.actor.require_barber()
            else:
                screen.actor.require_admin()
            await state.clear()
            from appointment_booking.app.bot.handlers.customer import CustomerScreens

            await CustomerScreens(self.services).appointments(
                screen,
                {
                    **metadata,
                    "scope": "employee" if kind == "barber_date" else "admin",
                    "day": values["schedule_day"],
                    "page": 0,
                },
            )
            return
        if kind == "time_off":
            await self.services.admin.request_time_off(user_id, parsed)
            await state.clear()
            await screen.show(screen.t("time_off_sent"), [])
            return
        if kind == "service":
            await self.services.admin.save_service(
                user_id, parsed, identifier, metadata.get("revision")
            )
        elif kind == "employee_create":
            from datetime import time

            employee = await self.services.admin.create_employee(
                user_id,
                parsed,
                [UUID(i) for i in values["service_ids"]],
                values["weekdays"],
                [(time.fromisoformat(a), time.fromisoformat(b)) for a, b in values["intervals"]],
            )
            await state.clear()
            await screen.show(
                screen.t("employee_created"),
                [
                    [await screen.button(screen.t("invite"), "invite", {"id": str(employee.id)})],
                    [await screen.button(screen.t("employees"), "manage", {"kind": "employees"})],
                ],
            )
            return
        elif kind == "employee":
            employee = await self.services.admin.save_employee(
                user_id, parsed, identifier, metadata.get("revision")
            )
            if identifier is None:
                await state.clear()
                await screen.show(
                    screen.t("employee_created"),
                    [
                        [
                            await screen.button(
                                screen.t("assignments"),
                                "assignments",
                                {"employee_id": str(employee.id)},
                            )
                        ],
                        [
                            await screen.button(
                                screen.t("hours"),
                                "manage",
                                {"kind": "hours", "employee_id": str(employee.id)},
                            )
                        ],
                        [
                            await screen.button(
                                screen.t("invite"), "invite", {"id": str(employee.id)}
                            )
                        ],
                    ],
                )
                return
        elif kind == "settings":
            await self.services.admin.configure(user_id, parsed, metadata["revision"])
        elif kind == "hours":
            await self.services.admin.hours(user_id, parsed)
        elif kind == "exceptions":
            await self.services.admin.exception(user_id, parsed)
        elif kind == "blocks":
            await self.services.admin.block(user_id, parsed)
        elif kind == "holidays":
            await self.services.admin.holiday(
                user_id, date.fromisoformat(values["day"]), values["label"]
            )
        elif kind == "profile":
            await self.services.users.profile(user_id, parsed)
        elif kind == "customers":
            await state.clear()
            await self.customers(screen, {"term": values["term"]})
            return
        elif kind == "stats":
            stats = await self.services.admin.statistics(
                user_id, date.fromisoformat(values["first"]), date.fromisoformat(values["last"])
            )
            # Business-local 'today' is obtained from the catalog timezone below.
            from zoneinfo import ZoneInfo

            from appointment_booking.app.core.clock import utcnow

            settings = await self.services.catalog.settings(user_id)
            today = utcnow().astimezone(ZoneInfo(settings.timezone)).date()
            today_stats = await self.services.admin.statistics(user_id, today, today)
            lines = [
                f"{screen.t('total')}: {stats['total']}",
                f"{screen.t('today')}: {today_stats['total']}",
                f"{screen.t('upcoming')}: {stats['upcoming']}",
            ]
            lines.extend(f"{screen.t(k)}: {v}" for k, v in stats["counts"].items())
            lines.append(screen.t("popular"))
            lines.extend(safe(n.get("uk", "Послуга")) + f": {c}" for n, c in stats["popular"])
            lines.append(screen.t("workload"))
            lines.extend(safe(n) + f": {c} / {m}" for n, c, m in stats["workload"])
            await state.clear()
            await screen.show("\n".join(lines), [])
            return
        elif kind == "reason":
            await state.clear()
            action = metadata["return_action"]
            data = {**metadata["return_data"], "reason": values["reason"]}
            from appointment_booking.app.bot.handlers.customer import CustomerScreens

            customer = CustomerScreens(self.services)
            if action == "reschedule":
                await customer.reschedule(screen, data)
            else:
                await customer.change_review(screen, data)
            return
        else:
            raise StaleSelection()
        await state.clear()
        await screen.show(screen.t("saved"), [])

    async def customers(self, screen: Screen, data: dict[str, Any]) -> None:
        page = int(data.get("page", 0))
        users = await self.services.admin.customers(screen.actor.user.id, data["term"], page)
        lines = [
            f"{safe(u.display_name)}\n{u.id}\n{u.telegram_id}\n{safe(u.phone or '—')}"
            for u in users
        ]
        await screen.show(
            "\n\n".join(lines) or screen.t("empty"),
            [await screen.pages("customers_page", data, page, len(users) == 8)],
        )

    async def time_off(self, screen: Screen, data: dict[str, Any]) -> None:
        from zoneinfo import ZoneInfo

        if not screen.actor.admin:
            screen.actor.require_barber()
        page = int(data.get("page", 0))
        items = await self.services.admin.time_off_requests(screen.actor.user.id, page)
        settings = await self.services.catalog.settings(screen.actor.user.id)
        zone = ZoneInfo(settings.timezone)
        lines = [screen.t("time_off_queue")]
        rows = []
        for item, name in items:
            label = safe(name) + " · " + item.starts_at.astimezone(zone).strftime("%d.%m %H:%M")
            lines.append(
                "\n"
                + label
                + " – "
                + item.ends_at.astimezone(zone).strftime("%d.%m %H:%M")
                + "\n"
                + safe(item.reason)
                + " · "
                + screen.t(item.status)
            )
            if screen.actor.admin:
                rows.append(
                    [
                        await screen.button(
                            screen.t(key) + " · " + name,
                            "time_off_review",
                            {"id": str(item.id), "approve": key == "approve"},
                        )
                        for key in ("approve", "reject")
                    ]
                )
        if not items:
            lines.append(screen.t("empty"))
        rows.append(await screen.pages("time_off_list", {}, page, len(items) == 8))
        await screen.show("\n".join(lines), rows)
