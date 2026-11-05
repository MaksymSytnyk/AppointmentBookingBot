import asyncio
from datetime import time
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from appointment_booking.app.core.clock import utcnow
from appointment_booking.app.core.config import get_settings
from appointment_booking.app.core.exceptions import PolicyViolation, ScheduleConflict
from appointment_booking.app.database.models import (
    BusinessSettings,
    Employee,
    EmployeeService,
    Service,
    WorkingHours,
)
from appointment_booking.app.database.repositories import AppointmentRepository, CatalogRepository
from appointment_booking.app.database.session import create_database


async def seed(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions.begin() as session:
        # One stable PostgreSQL advisory key serializes concurrent demo seed commands.
        from sqlalchemy import text

        await session.execute(text("SELECT pg_advisory_xact_lock(814728115)"))
        if await session.scalar(select(BusinessSettings.id)) is None:
            session.add(
                BusinessSettings(
                    name="ATELIER BARBER",
                    timezone="Europe/Kyiv",
                    contacts="Демонстраційний барбершоп. Адресу та телефон ще не налаштовано.",
                    about="ATELIER BARBER · Точна стрижка, догляд за бородою та увага до деталей.",
                    slot_minutes=15,
                    lead_minutes=60,
                    horizon_days=30,
                    cancellation_hours=12,
                )
            )
            for weekday in range(6):
                session.add_all(
                    [
                        WorkingHours(weekday=weekday, opens=time(9), closes=time(13)),
                        WorkingHours(weekday=weekday, opens=time(14), closes=time(19)),
                    ]
                )
        services = [
            (
                "haircut",
                {"uk": "Чоловіча стрижка"},
                {"uk": "Консультація, стрижка та укладання з урахуванням твого стилю."},
                45,
                45000,
            ),
            (
                "clipper",
                {"uk": "Стрижка машинкою"},
                {"uk": "Чітка форма та акуратні переходи."},
                30,
                30000,
            ),
            (
                "beard",
                {"uk": "Стрижка бороди"},
                {"uk": "Форма, контури та догляд за бородою."},
                30,
                25000,
            ),
            (
                "ritual",
                {"uk": "Стрижка + борода"},
                {"uk": "Комплексний догляд: стрижка, укладання та оформлення бороди."},
                75,
                65000,
            ),
            (
                "gray",
                {"uk": "Камуфлювання сивини"},
                {"uk": "Делікатне тонування для природного вигляду."},
                40,
                40000,
            ),
        ]
        for slug, name, description, duration, price in services:
            service_id = uuid5(NAMESPACE_URL, "atelier-demo/service/" + slug)
            await session.execute(
                insert(Service)
                .values(
                    id=service_id,
                    name=name,
                    description=description,
                    duration_minutes=duration,
                    buffer_before=0,
                    buffer_after=15,
                    price_minor=price,
                    currency="UAH",
                    active=True,
                    revision=1,
                )
                .on_conflict_do_nothing(index_elements=[Service.id])
            )
        # Serialize catalog changes with booking and preserve all existing appointment records.
        await session.flush()
        await CatalogRepository(session).settings(write=True)
        employee_id = uuid5(NAMESPACE_URL, "atelier-demo/employee/Alex Atelier")
        retired_id = uuid5(NAMESPACE_URL, "atelier-demo/employee/Danylo Atelier")
        retired = await session.get(Employee, retired_id)
        if retired and retired.user_id is None:
            future = await AppointmentRepository(session).future(utcnow(), retired.id)
            if future:
                raise ScheduleConflict([item.reference for item in future])
            retired.active = False
            retired.is_demo = True
            retired.revision += 1
        demo = await session.get(Employee, employee_id)
        if demo is not None and demo.user_id is not None:
            raise PolicyViolation(
                "The demo ID is linked to a real account; preserve it for manual review"
            )
        if demo is None:
            demo = Employee(id=employee_id, revision=1)
            session.add(demo)
        demo.name = "Тестовий барбер (не реальний акаунт)"
        demo.bio = (
            "Демонстраційний профіль для перевірки бота. Не реальна людина й не Telegram-акаунт."
        )
        demo.is_demo = True
        demo.active = True
        await session.flush()
        for slug, *_ in services:
            await session.execute(
                insert(EmployeeService)
                .values(
                    employee_id=employee_id,
                    service_id=uuid5(NAMESPACE_URL, "atelier-demo/service/" + slug),
                )
                .on_conflict_do_nothing()
            )


async def main() -> None:
    engine, sessions = create_database(get_settings().database_url.get_secret_value())
    try:
        await seed(sessions)
        print("Fictional demo catalog seeded (existing records preserved).")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
