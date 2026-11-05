from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from appointment_booking.app.database.models import Appointment, BusinessSettings, NotificationJob
from appointment_booking.app.database.repositories import (
    CatalogRepository,
    NotificationRepository,
    UserRepository,
)


async def enqueue_notifications(
    session: AsyncSession,
    appointment: Appointment,
    settings: BusinessSettings,
    kind: str,
    now: datetime,
    old_employee: UUID | None = None,
) -> None:
    await NotificationRepository(session).supersede(appointment.id)
    users = UserRepository(session)
    recipients = {appointment.customer_id}
    employees = {appointment.employee_id}
    if old_employee:
        employees.add(old_employee)
    for employee_id in employees:
        employee = await CatalogRepository(session).employee(employee_id)
        if employee.user_id:
            recipients.add(employee.user_id)
    if settings.notify_admins:
        recipients.update(await users.administrators())
    for recipient in recipients:
        session.add(
            NotificationJob(
                appointment_id=appointment.id,
                appointment_version=appointment.version,
                recipient_id=recipient,
                kind=kind,
                due_at=now,
                next_attempt_at=now,
            )
        )
    if appointment.status == "confirmed" and settings.reminders_enabled:
        for hours in settings.reminder_hours:
            due = appointment.starts_at - timedelta(hours=hours)
            if due > now:
                session.add(
                    NotificationJob(
                        appointment_id=appointment.id,
                        appointment_version=appointment.version,
                        recipient_id=appointment.customer_id,
                        kind=f"reminder_{hours}",
                        due_at=due,
                        next_attempt_at=due,
                    )
                )
