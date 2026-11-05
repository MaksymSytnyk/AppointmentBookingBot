# ATELIER BARBER architecture

## Layers and identity

Aiogram 3 middleware accepts private-chat updates, rate-limits the Telegram ID and resolves a fresh Actor. A User always receives CUSTOMER. OWNER is initialized only from trusted numeric configuration (`OWNER_TELEGRAM_ID`, with legacy `ADMIN_IDS` fallback). BARBER requires an active linked Employee and a BARBER grant. Services independently authorize every read/write; UI visibility and Redis callback contents never prove permission.

Screens call services; services own fresh SQLAlchemy transactions; repositories never commit. Domain functions implement scheduling, status and cutoff policies. All bot-owned prose is centralized in `bot/i18n/uk.json`; `text()` always renders Ukrainian regardless of Telegram locale. Historical multilingual JSON snapshots remain readable, with Ukrainian labels selected. Internal identifiers and technical data (references, IANA zones, currency codes, ISO timestamps) are not language options.

## Permissions

| Operation | CUSTOMER | BARBER | OWNER |
| --- | --- | --- | --- |
| Catalog / personal booking | Yes | Yes, personal context | Yes, personal context |
| Appointment details/events | Own | Own or assigned | All |
| Cancel appointment | Own, cutoff enforced | Assigned with separate grant, cutoff enforced | Any, audited reason for another customer/override |
| Reschedule | Own, cutoff enforced | Own customer booking only | All; changing barber reassigns atomically |
| Complete / no-show | No | Assigned, only after service end | Any, only after service end |
| Own recurring hours, blocks, exceptions | No | Explicit availability grant | Yes |
| Profile / weekly hours read | Own customer data | Own employee profile | Employee administration |
| Employees, prices, settings, customer search, overview | No | No | Yes |

`/owner`, `/barber`, `/customer` select a presentation view held in Redis for two hours. `/menu` and context home buttons use that view. Switching never changes the Actor or grants permissions; revoked roles are checked again at each update and service call. Customers' main menu contains exactly booking, own appointments, services, about and contacts.

## Persistence and migration

`0001` remains the immutable original schema. `0002` converts admin/employee grants to OWNER/BARBER, adds CUSTOMER grants for existing users, sets Ukrainian locales, adds cancellation permission and `employee_invitations`; `0003` adds personal-block/time-off permissions and `time_off_requests`. Existing records and foreign keys are preserved. Only recognized original demo English business text is translated; arbitrary authored content is preserved for owner review.

Employees keep names, specialization (`bio`), service assignments, active flag, schedule-edit, personal-block, time-off-request and cancellation permissions. Deactivation is soft; historical appointment relationships remain intact. An employee cannot be deactivated while any confirmed appointment ends in the future, including an in-progress visit. Owners receive the affected references and resolve those appointments first.

An invitation stores employee UUID, unique SHA-256 token hash, expiry, used timestamp and optional target numeric Telegram ID. The raw random token is returned once to the authorized owner for private transmission. No username is accepted as identity. Issuing/reissuing takes the settings write lock and employee lock, expires old unused tokens and audits issuance. Redemption takes settings, user and invitation locks; validates expiry, single use, optional recipient and employee eligibility; links the current Telegram-backed User and grants BARBER atomically. Existing active employment cannot be overwritten. Activation is audited without logging the bearer secret.

Employee creation saves profile, selected services and seven weekday overrides in one transaction after review. Invitation issuance is a separate explicit action. Editing a profile cannot replace its linked identity. Disabling an employee removes their BARBER grant; reactivation of the linked employee restores it.

Time-off requests belong to a barber and are visible only to that barber and the owner. Only enabled barbers can submit them. Owners review pending requests; approving creates a personal block under the settings lock and rejects any collision with confirmed appointments. Rejection and approval are audited. Existing availability grants retain personal-block permission during migration.

## Scheduling and transactions

The original PostgreSQL GiST exclusion constraint `appointments_no_overlap` still covers employee UUID and half-open UTC buffered occupancy for pending, confirmed, completed and no-show appointments. Check constraints bind occupancy to duration/buffer snapshots. `btree_gist` and immutable audit triggers remain intact.

Booking locks settings FOR SHARE, customer and target employee; revalidates active service, qualification, schedule and availability; writes appointment, creation/confirmation events, idempotency request and notification outbox atomically. Only the named overlap constraint maps to slot-unavailable. Retryable lock/serialization errors use bounded transaction retries.

Rescheduling locks settings, old/new employees in UUID order and appointment, rechecks assignment/version, excludes its old range during availability calculation, changes the same appointment and appends before/after audit plus outbox jobs. Failure rolls back the old reservation. Price, duration, buffers and service labels remain snapshots. Owner reassignment follows this same path and cannot bypass availability.

Schedule/config writers acquire the settings write lock, then employee locks as needed. Changes that invalidate future confirmed appointments are rejected. Employee availability operations re-resolve permission after the settings lock. Recurring business and employee intervals support lunch gaps; holidays, dated opening/closure overrides and UTC blocks are combined by precedence. Employee hours intersect business hours. Lead time and horizon use business-local dates. UTC elapsed duration, zoneinfo round-trip validation, distinct DST folds and explicit offset exception endpoints preserve DST correctness.

## Telegram and notification transport

Opaque callback tokens hold server-side action/data, actor ID, TTL and screen generation in Redis. Each navigation invalidates previous-screen tokens. Confirmation request tokens persist in PostgreSQL for replay after Redis loss. FSM forms use a review nonce; role checks run on form entry, input, review application and backend writes. Long lists and service selections are paginated. Context actions edit messages where possible; stale or missing screens have recovery paths.

Owner appointment filters combine local date, employee, service and status in SQL before pagination. Barber appointment lists always derive employee ID from the current Actor. Customer lists always derive customer ID from the Actor. Customer-owned records and assigned-barber records cannot be selected by arbitrary customer IDs in a callback.

Notification jobs remain a transactional outbox. Worker claims use SKIP LOCKED, random fenced lease tokens and bounded sends. Attempts, retries, permanent failures and superseded appointment versions persist. Ukrainian confirmation/reminder/change notifications use agreed service snapshots. Exactly-once external delivery is not promised because Telegram does not provide a sendMessage idempotency key.

## Verification and deployment

Unit tests cover DST, policies, validation and opaque callback security. Recording-session Aiogram tests cover real dispatch, Ukrainian menus, role switching, denied callbacks and booking review. Real PostgreSQL/Redis tests exercise constraints, contention, audit/outbox invariants, invitation replay/expiry/concurrency, permission scopes, filters and deactivation history.

Docker Compose retains migration, optional demo seed, bot and worker services; PostgreSQL/Redis are persistent. Apply migration and deploy bot/worker together while writers are stopped. Back up before upgrading. Secrets stay in configuration and are redacted in logs. Test databases must end in `_test`; real credentials are never needed for automated transport tests.
