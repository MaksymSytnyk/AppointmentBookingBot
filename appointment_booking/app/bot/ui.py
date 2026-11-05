from dataclasses import dataclass
from html import escape
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from appointment_booking.app.bot.texts import text
from appointment_booking.app.core.security import FlowStore
from appointment_booking.app.services.access import Actor


@dataclass
class Screen:
    event: Message | CallbackQuery
    actor: Actor
    flows: FlowStore

    @property
    def locale(self) -> str:
        return self.actor.user.locale

    def t(self, key: str, **values: object) -> str:
        return text(self.locale, key, **values)

    async def button(
        self, label: str, action: str, data: dict[str, Any] | None = None, token: str | None = None
    ) -> InlineKeyboardButton:
        callback = await self.flows.put(self.actor.user.telegram_id, action, data, token=token)
        return InlineKeyboardButton(text=label[:100], callback_data=callback)

    async def show(
        self,
        body: str,
        rows: list[list[InlineKeyboardButton]],
        *,
        home: bool = True,
        primary: bool = False,
    ) -> None:
        if primary and isinstance(self.event, Message):
            primary_keyboard = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text=button.text) for button in row] for row in rows if row
                ],
                resize_keyboard=True,
                input_field_placeholder=self.t("navigation_hint"),
            )
            await self.event.answer(body[:4000], reply_markup=primary_keyboard)
            return
        if home:
            rows.append([await self.button(self.t("home"), "home")])
        keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
        message = self.event.message if isinstance(self.event, CallbackQuery) else self.event
        if not isinstance(message, Message):
            return
        if isinstance(self.event, CallbackQuery):
            try:
                await message.edit_text(body[:4000], reply_markup=keyboard)
                return
            except TelegramBadRequest as exc:
                if "message is not modified" in str(exc):
                    return
                if not any(
                    part in str(exc)
                    for part in ("message to edit not found", "message can't be edited")
                ):
                    raise
        await message.answer(body[:4000], reply_markup=keyboard)

    async def pages(
        self, action: str, data: dict[str, Any], page: int, has_next: bool
    ) -> list[InlineKeyboardButton]:
        buttons = []
        if page > 0:
            buttons.append(await self.button(self.t("prev"), action, {**data, "page": page - 1}))
        if has_next:
            buttons.append(await self.button(self.t("next"), action, {**data, "page": page + 1}))
        return buttons


def safe(value: object) -> str:
    return escape(str(value))


def money(minor: int) -> str:
    whole, fraction = divmod(minor, 100)
    return str(whole) if fraction == 0 else f"{whole},{fraction:02}"
