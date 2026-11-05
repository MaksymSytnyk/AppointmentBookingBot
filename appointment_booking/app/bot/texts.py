import json
from importlib.resources import files

_TRANSLATIONS: dict[str, dict[str, str]] = {
    "uk": json.loads(files("appointment_booking.app.bot.i18n").joinpath("uk.json").read_text())
}


def text(locale: str, key: str, **values: object) -> str:
    return _TRANSLATIONS["uk"][key].format(**values)
