import json
import logging
import re
from datetime import UTC, datetime

TOKEN = re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b")
CREDENTIALS = re.compile(r"(postgres(?:ql)?(?:\+asyncpg)?|redis)://[^\s]+")


class SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Never serialize exception values or Telegram payloads. Explicit error categories only.
        message = TOKEN.sub("[REDACTED]", record.getMessage())
        message = CREDENTIALS.sub("[REDACTED]", message)
        payload = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        for field in ("event", "operation", "outcome", "duration_ms", "correlation_id"):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
    for name in ("aiogram.event", "aiohttp.access", "sqlalchemy.engine"):
        logging.getLogger(name).setLevel(logging.WARNING)
