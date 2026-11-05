from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from appointment_booking.app.database.models import User
from appointment_booking.app.database.repositories import UserRepository
from appointment_booking.app.domain.dto import ProfileInput
from appointment_booking.app.services.access import Actor, actor_for


class UserService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], admins: list[int]) -> None:
        self.sessions, self.admins = sessions, admins

    async def identify(self, telegram_id: int, name: str, locale: str) -> Actor:
        async with self.sessions.begin() as session:
            user = await UserRepository(session).identify(
                telegram_id, name, "uk", telegram_id in self.admins
            )
            return await actor_for(session, user.id)

    async def profile(self, user_id: UUID, data: ProfileInput) -> User:
        async with self.sessions.begin() as session:
            actor = await actor_for(session, user_id)
            for key, value in data.model_dump().items():
                setattr(actor.user, key, value)
            return actor.user
