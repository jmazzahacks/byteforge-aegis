-- ByteForge Aegis Database Schema (Multi-tenant Authentication Service)
--
-- Identifiers are UUIDs (post-contract): sites, users, and webhook_events are
-- keyed by an application-generated UUIDv7 (PostgreSQL cannot generate v7
-- natively before PG18). Token tables keep a surrogate int id PK — it is never
-- exposed through the API and carries no cross-install merge risk — and
-- reference their owners by UUID.

-- User role enumeration
CREATE TYPE user_role AS ENUM ('user', 'admin');

-- Sites table (tenants/websites)
CREATE TABLE IF NOT EXISTS sites (
    uuid UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    domain VARCHAR(255) UNIQUE NOT NULL,
    frontend_url VARCHAR(255) NOT NULL,
    verification_redirect_url VARCHAR(255),
    email_from VARCHAR(255) NOT NULL,
    email_from_name VARCHAR(255) NOT NULL,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    allow_self_registration BOOLEAN DEFAULT TRUE NOT NULL,
    webhook_url VARCHAR(512),
    webhook_secret VARCHAR(255),
    tenant_api_key VARCHAR(64) NOT NULL,
    mailgun_domain VARCHAR(255),
    mailgun_api_key VARCHAR(255),
    -- When true, no user on this site may be deleted and the site itself may
    -- not be deleted. For tenants where every account anchors records whose
    -- loss is unrecoverable (custody, financial), so the guarantee does not
    -- depend on remembering to mark each user individually.
    deletion_protected BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_sites_domain ON sites(domain);

-- Users table (scoped to sites)
CREATE TABLE IF NOT EXISTS users (
    uuid UUID PRIMARY KEY,
    site_uuid UUID NOT NULL REFERENCES sites(uuid) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255),
    is_verified BOOLEAN DEFAULT FALSE,
    role user_role DEFAULT 'user',
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    -- When true, admin deletion of this user is refused. For accounts whose
    -- downstream records hold real value that would become unattributable
    -- if the Aegis identity disappeared.
    deletion_protected BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE(site_uuid, email)
);

CREATE INDEX idx_users_site_uuid ON users(site_uuid);
CREATE INDEX idx_users_email ON users(email);

-- Auth tokens table (for session management)
CREATE TABLE IF NOT EXISTS auth_tokens (
    id SERIAL PRIMARY KEY,
    site_uuid UUID NOT NULL REFERENCES sites(uuid) ON DELETE CASCADE,
    user_uuid UUID NOT NULL REFERENCES users(uuid) ON DELETE CASCADE,
    token VARCHAR(255) UNIQUE NOT NULL,
    expires_at BIGINT NOT NULL,
    created_at BIGINT NOT NULL
);

CREATE INDEX idx_auth_tokens_token ON auth_tokens(token);
CREATE INDEX idx_auth_tokens_user_uuid ON auth_tokens(user_uuid);
CREATE INDEX idx_auth_tokens_site_uuid ON auth_tokens(site_uuid);
CREATE INDEX idx_auth_tokens_expires_at ON auth_tokens(expires_at);

-- Refresh tokens table (for long-lived session refresh)
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id SERIAL PRIMARY KEY,
    site_uuid UUID NOT NULL REFERENCES sites(uuid) ON DELETE CASCADE,
    user_uuid UUID NOT NULL REFERENCES users(uuid) ON DELETE CASCADE,
    token VARCHAR(255) UNIQUE NOT NULL,
    family_id VARCHAR(255) NOT NULL,
    expires_at BIGINT NOT NULL,
    created_at BIGINT NOT NULL,
    used_at BIGINT,
    revoked BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_refresh_tokens_token ON refresh_tokens(token);
CREATE INDEX idx_refresh_tokens_user_uuid ON refresh_tokens(user_uuid);
CREATE INDEX idx_refresh_tokens_site_uuid ON refresh_tokens(site_uuid);
CREATE INDEX idx_refresh_tokens_family_id ON refresh_tokens(family_id);
CREATE INDEX idx_refresh_tokens_expires_at ON refresh_tokens(expires_at);

-- Email verification tokens table
CREATE TABLE IF NOT EXISTS email_verification_tokens (
    id SERIAL PRIMARY KEY,
    site_uuid UUID NOT NULL REFERENCES sites(uuid) ON DELETE CASCADE,
    user_uuid UUID NOT NULL REFERENCES users(uuid) ON DELETE CASCADE,
    token VARCHAR(255) UNIQUE NOT NULL,
    expires_at BIGINT NOT NULL,
    created_at BIGINT NOT NULL
);

CREATE INDEX idx_email_verification_tokens_token ON email_verification_tokens(token);
CREATE INDEX idx_email_verification_tokens_user_uuid ON email_verification_tokens(user_uuid);
CREATE INDEX idx_email_verification_tokens_site_uuid ON email_verification_tokens(site_uuid);

-- Password reset tokens table
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id SERIAL PRIMARY KEY,
    site_uuid UUID NOT NULL REFERENCES sites(uuid) ON DELETE CASCADE,
    user_uuid UUID NOT NULL REFERENCES users(uuid) ON DELETE CASCADE,
    token VARCHAR(255) UNIQUE NOT NULL,
    expires_at BIGINT NOT NULL,
    created_at BIGINT NOT NULL,
    used BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_password_reset_tokens_token ON password_reset_tokens(token);
CREATE INDEX idx_password_reset_tokens_user_uuid ON password_reset_tokens(user_uuid);
CREATE INDEX idx_password_reset_tokens_site_uuid ON password_reset_tokens(site_uuid);

-- Email change requests table
CREATE TABLE IF NOT EXISTS email_change_requests (
    id SERIAL PRIMARY KEY,
    site_uuid UUID NOT NULL REFERENCES sites(uuid) ON DELETE CASCADE,
    user_uuid UUID NOT NULL REFERENCES users(uuid) ON DELETE CASCADE,
    new_email VARCHAR(255) NOT NULL,
    token VARCHAR(255) UNIQUE NOT NULL,
    expires_at BIGINT NOT NULL,
    created_at BIGINT NOT NULL
);

CREATE INDEX idx_email_change_requests_token ON email_change_requests(token);
CREATE INDEX idx_email_change_requests_user_uuid ON email_change_requests(user_uuid);
CREATE INDEX idx_email_change_requests_site_uuid ON email_change_requests(site_uuid);

-- Webhooks a tenant is still owed. Written BEFORE the first HTTP attempt:
-- delivery used to be a bare submit to an in-process thread pool, and the
-- log row was written only after the POST, so anything queued or in flight
-- when the container rotated vanished leaving no evidence it ever existed.
-- Persisting first makes a restart cost latency instead of data.
--
-- event_id is the primary key, so re-raising the same event conflicts
-- rather than queueing a second delivery.
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    event_id UUID PRIMARY KEY,
    site_uuid UUID NOT NULL REFERENCES sites(uuid) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,
    -- Stored verbatim: the HMAC is computed over these exact bytes, so
    -- re-serializing could reorder keys and invalidate the signature.
    payload TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    -- Doubles as a lease. Claiming pushes this forward, so a worker that
    -- dies mid-attempt releases the row when the lease expires — there is
    -- no in-flight state to get stuck in and nothing to reap.
    next_attempt_at BIGINT NOT NULL,
    last_status INTEGER,
    last_error TEXT,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);

-- The claim query's index: only pending rows are ever scanned for work.
CREATE INDEX idx_webhook_deliveries_due
    ON webhook_deliveries(next_attempt_at) WHERE status = 'pending';
CREATE INDEX idx_webhook_deliveries_site_uuid ON webhook_deliveries(site_uuid);
CREATE INDEX idx_webhook_deliveries_status ON webhook_deliveries(status);

-- Webhook delivery log — one row per ATTEMPT.
--
-- uuid identifies the attempt; event_id identifies the event and is stable
-- across retries. They were the same column before retries existed, which
-- is precisely why a second attempt could not be logged: it collided on the
-- primary key. event_id is what a tenant reports, so it carries the index.
CREATE TABLE IF NOT EXISTS webhook_events (
    uuid UUID PRIMARY KEY,
    -- Nullable on purpose. The pre-retry code inserts log rows without this
    -- column, so NOT NULL would make every webhook log write fail under a
    -- rollback to that image — silently, since the insert is wrapped in a
    -- catch-all. Readers fall back to uuid, which is what a row written by
    -- that code means anyway.
    event_id UUID,
    site_uuid UUID NOT NULL REFERENCES sites(uuid) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,
    payload TEXT NOT NULL,
    response_status INTEGER,
    response_body TEXT,
    success BOOLEAN NOT NULL DEFAULT FALSE,
    attempt INTEGER NOT NULL DEFAULT 1,
    created_at BIGINT NOT NULL
);

CREATE INDEX idx_webhook_events_site_uuid ON webhook_events(site_uuid);
CREATE INDEX idx_webhook_events_event_type ON webhook_events(event_type);
CREATE INDEX idx_webhook_events_created_at ON webhook_events(created_at);
CREATE INDEX idx_webhook_events_event_id ON webhook_events(event_id);
