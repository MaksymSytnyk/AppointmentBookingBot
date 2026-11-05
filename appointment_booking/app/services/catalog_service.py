from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from appointment_booking.app.core.exceptions import NotFound
from appointment_booking.app.database.models import BusinessSettings, Employee, Service
from appointment_booking.app.database.repositories import CatalogRepository
from appointment_booking.app.services.access import actor_for


class CatalogService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def settings(self, user_id: UUID) -> BusinessSettings:
        async with self.sessions() as session:
            await actor_for(session, user_id)
            return await CatalogRepository(session).settings()

    async def services(self, user_id: UUID, page: int = 0) -> list[Service]:
        async with self.sessions() as session:
            await actor_for(session, user_id)
            return await CatalogRepository(session).services(page=max(0, page))

    async def service(self, user_id: UUID, identifier: UUID) -> Service:
        async with self.sessions() as session:
            await actor_for(session, user_id)
            item = await CatalogRepository(session).service(identifier)
            if not item.active:
                raise NotFound()
            return item

    async def employees(self, user_id: UUID, service_id: UUID, page: int = 0) -> list[Employee]:
        async with self.sessions() as session:
            await actor_for(session, user_id)
            return await CatalogRepository(session).employees(service_id, max(0, page))
