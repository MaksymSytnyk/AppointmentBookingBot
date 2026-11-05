from dataclasses import dataclass

from appointment_booking.app.services.admin_service import AdminService
from appointment_booking.app.services.availability_service import AvailabilityService
from appointment_booking.app.services.booking_service import BookingService
from appointment_booking.app.services.catalog_service import CatalogService
from appointment_booking.app.services.user_service import UserService


@dataclass(frozen=True)
class Services:
    users: UserService
    catalog: CatalogService
    availability: AvailabilityService
    bookings: BookingService
    admin: AdminService
