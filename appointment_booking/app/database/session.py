from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_database(url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        url,
        pool_size=10,
        max_overflow=10,
        pool_pre_ping=True,
        isolation_level="READ COMMITTED",
        connect_args={
            "server_settings": {
                "timezone": "UTC",
                "statement_timeout": "15000",
                "lock_timeout": "5000",
            }
        },
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)
