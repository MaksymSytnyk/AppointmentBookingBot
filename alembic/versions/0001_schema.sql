
CREATE TABLE users (
	telegram_id BIGINT NOT NULL, 
	display_name VARCHAR(100) NOT NULL, 
	locale VARCHAR(2) NOT NULL, 
	phone VARCHAR(20), 
	active BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_users PRIMARY KEY (id), 
	CONSTRAINT ck_users_locale CHECK (locale IN ('en', 'uk')), 
	CONSTRAINT uq_users_telegram_id UNIQUE (telegram_id)
)

;

CREATE TABLE services (
	name JSONB NOT NULL, 
	description JSONB NOT NULL, 
	duration_minutes INTEGER NOT NULL, 
	buffer_before INTEGER NOT NULL, 
	buffer_after INTEGER NOT NULL, 
	price_minor INTEGER NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	active BOOLEAN NOT NULL, 
	revision INTEGER NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_services PRIMARY KEY (id), 
	CONSTRAINT ck_services_duration CHECK (duration_minutes BETWEEN 1 AND 1440), 
	CONSTRAINT ck_services_buffers CHECK (buffer_before BETWEEN 0 AND 1440 AND buffer_after BETWEEN 0 AND 1440), 
	CONSTRAINT ck_services_price CHECK (price_minor >= 0), 
	CONSTRAINT ck_services_currency CHECK (currency ~ '^[A-Z]{3}$')
)

;

CREATE TABLE business_settings (
	singleton BOOLEAN NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	about TEXT NOT NULL, 
	contacts TEXT NOT NULL, 
	timezone VARCHAR(80) NOT NULL, 
	slot_minutes INTEGER NOT NULL, 
	lead_minutes INTEGER NOT NULL, 
	horizon_days INTEGER NOT NULL, 
	cancellation_hours INTEGER NOT NULL, 
	notify_admins BOOLEAN NOT NULL, 
	reminders_enabled BOOLEAN NOT NULL, 
	reminder_hours JSONB NOT NULL, 
	revision INTEGER NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_business_settings PRIMARY KEY (id), 
	CONSTRAINT ck_business_settings_singleton CHECK (singleton), 
	CONSTRAINT ck_business_settings_slots CHECK (slot_minutes BETWEEN 1 AND 120), 
	CONSTRAINT ck_business_settings_lead CHECK (lead_minutes BETWEEN 0 AND 43200), 
	CONSTRAINT ck_business_settings_horizon CHECK (horizon_days BETWEEN 1 AND 365), 
	CONSTRAINT ck_business_settings_cancellation CHECK (cancellation_hours BETWEEN 0 AND 720), 
	CONSTRAINT uq_business_settings_singleton UNIQUE (singleton)
)

;

CREATE TABLE working_hours (
	weekday INTEGER NOT NULL, 
	opens TIME WITHOUT TIME ZONE NOT NULL, 
	closes TIME WITHOUT TIME ZONE NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_working_hours PRIMARY KEY (id), 
	CONSTRAINT ck_working_hours_weekday CHECK (weekday BETWEEN 0 AND 6), 
	CONSTRAINT ck_working_hours_interval CHECK (closes > opens), 
	CONSTRAINT uq_working_hours_weekday UNIQUE (weekday, opens)
)

;

CREATE TABLE holidays (
	day DATE NOT NULL, 
	label VARCHAR(100) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_holidays PRIMARY KEY (id), 
	CONSTRAINT uq_holidays_day UNIQUE (day)
)

;

CREATE TABLE user_roles (
	user_id UUID NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	CONSTRAINT pk_user_roles PRIMARY KEY (user_id, role), 
	CONSTRAINT ck_user_roles_role CHECK (role IN ('admin', 'employee')), 
	CONSTRAINT fk_user_roles_user_id_users FOREIGN KEY(user_id) REFERENCES users (id)
)

;

CREATE TABLE employees (
	user_id UUID, 
	name VARCHAR(100) NOT NULL, 
	bio TEXT NOT NULL, 
	active BOOLEAN NOT NULL, 
	may_manage_availability BOOLEAN NOT NULL, 
	revision INTEGER NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_employees PRIMARY KEY (id), 
	CONSTRAINT uq_employees_user_id UNIQUE (user_id), 
	CONSTRAINT fk_employees_user_id_users FOREIGN KEY(user_id) REFERENCES users (id)
)

;

CREATE TABLE admin_audit (
	actor_id UUID NOT NULL, 
	action VARCHAR(80) NOT NULL, 
	entity_id UUID, 
	details JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_admin_audit PRIMARY KEY (id), 
	CONSTRAINT fk_admin_audit_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id)
)

;
CREATE INDEX ix_admin_audit_actor_id ON admin_audit (actor_id);

CREATE TABLE employee_services (
	employee_id UUID NOT NULL, 
	service_id UUID NOT NULL, 
	CONSTRAINT pk_employee_services PRIMARY KEY (employee_id, service_id), 
	CONSTRAINT fk_employee_services_employee_id_employees FOREIGN KEY(employee_id) REFERENCES employees (id), 
	CONSTRAINT fk_employee_services_service_id_services FOREIGN KEY(service_id) REFERENCES services (id)
)

;

CREATE TABLE employee_working_hours (
	employee_id UUID NOT NULL, 
	weekday INTEGER NOT NULL, 
	closed BOOLEAN NOT NULL, 
	opens TIME WITHOUT TIME ZONE, 
	closes TIME WITHOUT TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_employee_working_hours PRIMARY KEY (id), 
	CONSTRAINT ck_employee_working_hours_weekday CHECK (weekday BETWEEN 0 AND 6), 
	CONSTRAINT ck_employee_working_hours_interval CHECK ((closed AND opens IS NULL AND closes IS NULL) OR (NOT closed AND opens IS NOT NULL AND closes IS NOT NULL AND closes > opens)), 
	CONSTRAINT fk_employee_working_hours_employee_id_employees FOREIGN KEY(employee_id) REFERENCES employees (id)
)

;
CREATE INDEX ix_employee_working_hours_employee_id ON employee_working_hours (employee_id);

CREATE TABLE schedule_exceptions (
	employee_id UUID, 
	day DATE NOT NULL, 
	closed BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_schedule_exceptions PRIMARY KEY (id), 
	CONSTRAINT uq_schedule_exceptions_employee_id UNIQUE (employee_id, day), 
	CONSTRAINT fk_schedule_exceptions_employee_id_employees FOREIGN KEY(employee_id) REFERENCES employees (id)
)

;
CREATE INDEX ix_schedule_exceptions_employee_id ON schedule_exceptions (employee_id);
CREATE UNIQUE INDEX uq_exception_business_day ON schedule_exceptions (day) WHERE employee_id IS NULL;

CREATE TABLE blocked_periods (
	employee_id UUID, 
	starts_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ends_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	reason VARCHAR(300) NOT NULL, 
	creator_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_blocked_periods PRIMARY KEY (id), 
	CONSTRAINT ck_blocked_periods_interval CHECK (ends_at > starts_at), 
	CONSTRAINT fk_blocked_periods_employee_id_employees FOREIGN KEY(employee_id) REFERENCES employees (id), 
	CONSTRAINT fk_blocked_periods_creator_id_users FOREIGN KEY(creator_id) REFERENCES users (id)
)

;
CREATE INDEX ix_blocked_periods_employee_id ON blocked_periods (employee_id);

CREATE TABLE appointments (
	customer_id UUID NOT NULL, 
	employee_id UUID NOT NULL, 
	service_id UUID NOT NULL, 
	reference VARCHAR(16) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	starts_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ends_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	occupied_start TIMESTAMP WITH TIME ZONE NOT NULL, 
	occupied_end TIMESTAMP WITH TIME ZONE NOT NULL, 
	duration_minutes INTEGER NOT NULL, 
	buffer_before INTEGER NOT NULL, 
	buffer_after INTEGER NOT NULL, 
	price_minor INTEGER NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	service_label JSONB NOT NULL, 
	version INTEGER NOT NULL, 
	cancelled_by UUID, 
	cancellation_reason VARCHAR(300), 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_appointments PRIMARY KEY (id), 
	CONSTRAINT ck_appointments_status CHECK (status IN ('pending','confirmed','cancelled','completed','no_show','expired')), 
	CONSTRAINT ck_appointments_snapshots CHECK (duration_minutes > 0 AND buffer_before >= 0 AND buffer_after >= 0 AND price_minor >= 0 AND version > 0), 
	CONSTRAINT ck_appointments_occupancy CHECK (ends_at = starts_at + duration_minutes * interval '1 minute' AND occupied_start = starts_at - buffer_before * interval '1 minute' AND occupied_end = ends_at + buffer_after * interval '1 minute'), 
	CONSTRAINT appointments_no_overlap EXCLUDE USING gist (employee_id WITH =, tstzrange(occupied_start, occupied_end, '[)') WITH &&) WHERE (status IN ('pending','confirmed','completed','no_show')), 
	CONSTRAINT fk_appointments_customer_id_users FOREIGN KEY(customer_id) REFERENCES users (id), 
	CONSTRAINT fk_appointments_employee_id_employees FOREIGN KEY(employee_id) REFERENCES employees (id), 
	CONSTRAINT fk_appointments_service_id_services FOREIGN KEY(service_id) REFERENCES services (id), 
	CONSTRAINT uq_appointments_reference UNIQUE (reference), 
	CONSTRAINT fk_appointments_cancelled_by_users FOREIGN KEY(cancelled_by) REFERENCES users (id)
)

;
CREATE INDEX ix_appointment_customer_start ON appointments (customer_id, starts_at);
CREATE INDEX ix_appointment_status_start ON appointments (status, starts_at);
CREATE INDEX ix_appointment_employee_start ON appointments (employee_id, starts_at);

CREATE TABLE exception_intervals (
	exception_id UUID NOT NULL, 
	starts_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ends_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_exception_intervals PRIMARY KEY (id), 
	CONSTRAINT ck_exception_intervals_interval CHECK (ends_at > starts_at), 
	CONSTRAINT fk_exception_intervals_exception_id_schedule_exceptions FOREIGN KEY(exception_id) REFERENCES schedule_exceptions (id) ON DELETE CASCADE
)

;
CREATE INDEX ix_exception_intervals_exception_id ON exception_intervals (exception_id);

CREATE TABLE appointment_events (
	appointment_id UUID NOT NULL, 
	actor_id UUID NOT NULL, 
	action VARCHAR(40) NOT NULL, 
	version INTEGER NOT NULL, 
	details JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_appointment_events PRIMARY KEY (id), 
	CONSTRAINT fk_appointment_events_appointment_id_appointments FOREIGN KEY(appointment_id) REFERENCES appointments (id), 
	CONSTRAINT fk_appointment_events_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id)
)

;
CREATE INDEX ix_appointment_events_appointment_id ON appointment_events (appointment_id);

CREATE TABLE booking_requests (
	customer_id UUID NOT NULL, 
	token VARCHAR(64) NOT NULL, 
	fingerprint VARCHAR(64) NOT NULL, 
	appointment_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_booking_requests PRIMARY KEY (id), 
	CONSTRAINT uq_booking_requests_customer_id UNIQUE (customer_id, token), 
	CONSTRAINT fk_booking_requests_customer_id_users FOREIGN KEY(customer_id) REFERENCES users (id), 
	CONSTRAINT fk_booking_requests_appointment_id_appointments FOREIGN KEY(appointment_id) REFERENCES appointments (id)
)

;

CREATE TABLE notification_jobs (
	appointment_id UUID NOT NULL, 
	appointment_version INTEGER NOT NULL, 
	recipient_id UUID NOT NULL, 
	kind VARCHAR(40) NOT NULL, 
	due_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	attempts INTEGER NOT NULL, 
	lease_token UUID, 
	lease_until TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_notification_jobs PRIMARY KEY (id), 
	CONSTRAINT uq_notification_jobs_appointment_id UNIQUE (appointment_id, appointment_version, recipient_id, kind), 
	CONSTRAINT ck_notification_jobs_state CHECK (state IN ('queued','sending','sent','dead','superseded')), 
	CONSTRAINT ck_notification_jobs_attempts CHECK (attempts >= 0), 
	CONSTRAINT fk_notification_jobs_appointment_id_appointments FOREIGN KEY(appointment_id) REFERENCES appointments (id), 
	CONSTRAINT fk_notification_jobs_recipient_id_users FOREIGN KEY(recipient_id) REFERENCES users (id)
)

;
CREATE INDEX ix_jobs_due ON notification_jobs (next_attempt_at) WHERE state = 'queued';
CREATE INDEX ix_notification_jobs_appointment_id ON notification_jobs (appointment_id);
CREATE INDEX ix_jobs_leases ON notification_jobs (lease_until) WHERE state = 'sending';

CREATE TABLE notification_logs (
	job_id UUID NOT NULL, 
	attempt INTEGER NOT NULL, 
	outcome VARCHAR(40) NOT NULL, 
	telegram_message_id BIGINT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_notification_logs PRIMARY KEY (id), 
	CONSTRAINT uq_notification_logs_job_id UNIQUE (job_id, attempt), 
	CONSTRAINT fk_notification_logs_job_id_notification_jobs FOREIGN KEY(job_id) REFERENCES notification_jobs (id)
)

;
CREATE INDEX ix_notification_logs_job_id ON notification_logs (job_id);
