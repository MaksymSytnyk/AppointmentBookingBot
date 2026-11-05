from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from appointment_booking.app.database.repositories import UserRepository
from appointment_booking.app.domain.access import Actor as Actor


async def actor_for(session: AsyncSession, user_id: UUID) -> Actor:
    repo = UserRepository(session)
    user = await repo.get(user_id)
    roles = frozenset(await repo.roles(user_id))
    employee = await repo.employee(user_id) if "BARBER" in roles else None
    return Actor(user, roles, employee)
