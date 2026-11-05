from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    bot_token: SecretStr = SecretStr("")
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://atelier:change-me@localhost:5432/atelier"
    )
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    admin_ids: list[int] = Field(default_factory=list)
    owner_telegram_id: int | None = Field(default=None, gt=0)
    log_level: str = "INFO"

    @field_validator("owner_telegram_id", mode="before")
    @classmethod
    def optional_owner(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("database_url")
    @classmethod
    def database_is_async(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL requires PostgreSQL asyncpg")
        return value

    @field_validator("admin_ids")
    @classmethod
    def positive_ids(cls, value: list[int]) -> list[int]:
        if any(x <= 0 for x in value):
            raise ValueError("Administrator IDs must be positive")
        return list(set(value))

    def require_token(self) -> str:
        token = self.bot_token.get_secret_value()
        if not token:
            raise ValueError("Set BOT_TOKEN in your environment")
        return token


@lru_cache
def get_settings() -> Settings:
    return Settings()
