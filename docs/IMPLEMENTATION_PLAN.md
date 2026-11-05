# ATELIER BARBER refinement implementation

1. Inspected original repository, PostgreSQL schema, services, Redis flow protocol, Telegram transport, tests, Docker setup and documents.
2. Preserved scheduling/domain logic, transaction boundaries, snapshots, PostgreSQL occupancy constraints, audit triggers and persistent notification outbox.
3. Added migrations 0002/0003: explicit CUSTOMER/BARBER/OWNER grants, Ukrainian stored locales, invitation lifecycle and separate employee cancellation permission. Existing business records and history remain.
4. Centralized Ukrainian product language; removed English resources and language forms. Split private owner, barber and customer menus with explicit view-switching commands.
5. Added employee creation with service checkbox selection, weekdays, split working intervals, review and atomic persistence; employee editing, protected invitations and confirmed soft deactivation.
6. Added barber profile/settings, selected-day/week schedules, owner overview and composable SQL appointment filters. Kept rescheduling/reassignment on the existing audited atomic operation.
7. Added service and transport tests for role boundaries, callbacks, menu localization, invitations, concurrent redemption, filters and history preservation. Updated original tests to the explicit role model.
8. Updated configurable Ukrainian demo catalog, environment documentation and installation instructions.

The README records operational boundaries. Invitation recipient-ID binding is implemented in the service; UI links are private bearer invitations. Recurring hours and exceptions share a schedule-edit permission. Personal blocks, time-off requests and appointment cancellation have separate permissions. Owners review leave requests and approved requests become conflict-checked blocks. There is no bulk appointment reassignment.
