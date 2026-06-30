-- ============================================================
-- auth_schema.sql
-- ============================================================
-- Schema for the DWS authentication / RBAC subsystem.
--
-- Scope note: this file ONLY creates tables prefixed `auth_` plus
-- `roles` and `permissions` (also auth-owned, unprefixed because
-- they are referenced by name throughout the API/UI layer). It
-- does NOT touch `transactions`, `sessions` (shift/work-session
-- stats owned by database_node.py), `errors`, `calibration_*`,
-- `system_state`, or `health_status`.
--
-- IMPORTANT NAMING NOTE: database_node.py already creates a table
-- called `sessions` for *shift/work session* statistics (parcel
-- counts, error counts, latency — see DatabaseNode._create_schema).
-- That table is unrelated to login sessions and MUST NOT be
-- confused with this module's `auth_sessions` table, which stores
-- authentication session tokens. The two are intentionally named
-- differently to avoid collision.
--
-- All tables use `CREATE TABLE IF NOT EXISTS` so this script is
-- idempotent and safe to run against an existing database.
-- ============================================================

PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------
-- roles
-- ------------------------------------------------------------
-- Seeded with the three default roles from the spec. `level` is a
-- simple numeric ranking used only for "is role A at least as
-- privileged as role B" comparisons in permissions.py; it is not
-- exposed to the UI and has no other special meaning.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS roles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    description TEXT,
    level       INTEGER NOT NULL DEFAULT 0,
    is_system   INTEGER NOT NULL DEFAULT 1,  -- 1 = built-in, cannot be deleted
    created_at  TEXT NOT NULL
);

-- ------------------------------------------------------------
-- permissions
-- ------------------------------------------------------------
-- Fine-grained permission keys (e.g. "settings.write",
-- "users.manage", "calibration.run", "export.run"). Roles are
-- mapped to permissions via role_permissions. This table is the
-- canonical list of permission keys the rest of the system may
-- check against.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS permissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT UNIQUE NOT NULL,
    description TEXT
);

-- ------------------------------------------------------------
-- role_permissions
-- ------------------------------------------------------------
-- Many-to-many join between roles and permissions.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id       INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

-- ------------------------------------------------------------
-- auth_users
-- ------------------------------------------------------------
-- Operator / supervisor / administrator accounts.
--
-- password_hash stores a bcrypt hash (60 bytes, ASCII) — never a
-- plaintext password and never a reversible encryption.
--
-- failed_attempts / locked_until implement account lockout.
-- must_change_password supports forced resets (admin reset, or
-- password_expires_at policy below).
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS auth_users (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    username              TEXT UNIQUE NOT NULL,
    display_name          TEXT,
    password_hash         TEXT NOT NULL,
    role_id               INTEGER NOT NULL REFERENCES roles(id),
    is_active             INTEGER NOT NULL DEFAULT 1,   -- 0 = disabled
    failed_attempts        INTEGER NOT NULL DEFAULT 0,
    locked_until          REAL,                          -- unix ts, NULL = not locked
    must_change_password  INTEGER NOT NULL DEFAULT 0,
    password_changed_at   TEXT,
    password_expires_at   TEXT,
    created_at            TEXT NOT NULL,
    created_by            TEXT,                          -- username of creator, NULL for seed admin
    updated_at            TEXT,
    last_login_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_auth_users_role
    ON auth_users(role_id);

-- ------------------------------------------------------------
-- auth_sessions
-- ------------------------------------------------------------
-- Login sessions, deliberately separate from the pre-existing
-- `sessions` (shift-stats) table — see header note above.
--
-- session_token stores a SHA-256 hash of the bearer token actually
-- handed to the client; the raw token itself is never persisted,
-- matching the password-hash-not-password principle: a stolen DB
-- backup does not yield usable bearer tokens.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS auth_sessions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    session_token_hash TEXT UNIQUE NOT NULL,
    user_id            INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    station_id         TEXT,
    ip_address         TEXT,
    user_agent         TEXT,
    created_at         REAL NOT NULL,     -- unix ts
    last_seen_at       REAL NOT NULL,     -- unix ts, updated on each validated request
    expires_at         REAL NOT NULL,     -- unix ts, absolute session lifetime
    revoked_at         REAL               -- unix ts, NULL = still valid
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
    ON auth_sessions(user_id);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires
    ON auth_sessions(expires_at);

-- ------------------------------------------------------------
-- auth_login_history
-- ------------------------------------------------------------
-- Append-only audit trail. Every row is one event. Never updated,
-- never deleted by application code (retention/pruning, if ever
-- desired, is an explicit operator action outside this module's
-- scope, matching the "database is never pruned" policy already
-- documented for `transactions`).
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS auth_login_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,           -- ISO 8601 UTC
    username    TEXT NOT NULL,           -- recorded even if user lookup fails
    user_id     INTEGER REFERENCES auth_users(id) ON DELETE SET NULL,
    ip_address  TEXT,
    action      TEXT NOT NULL,           -- login_success, login_failed, logout,
                                          -- password_change, password_reset,
                                          -- user_created, user_disabled,
                                          -- user_enabled, user_deleted,
                                          -- role_changed, account_locked
    result      TEXT NOT NULL,           -- success | failure
    detail      TEXT                     -- free-text context, never the password
);

CREATE INDEX IF NOT EXISTS idx_auth_login_history_username
    ON auth_login_history(username);

CREATE INDEX IF NOT EXISTS idx_auth_login_history_timestamp
    ON auth_login_history(timestamp);

-- ------------------------------------------------------------
-- Seed data: default roles
-- ------------------------------------------------------------
-- Permission seeding and the bootstrap administrator account are
-- handled in Python (auth_migration.py) rather than hardcoded SQL,
-- because the administrator needs a freshly-generated bcrypt hash
-- and a randomly generated first password rather than a fixed
-- value baked into version control.
-- ------------------------------------------------------------

INSERT OR IGNORE INTO roles (name, description, level, is_system, created_at)
VALUES
    ('operator',      'Dashboard, Live Scan, Transactions only',                 10, 1, datetime('now')),
    ('supervisor',     'Everything Operator can do, plus Reports, Diagnostics, Export', 50, 1, datetime('now')),
    ('administrator', 'Full system access including user and role management',  100, 1, datetime('now'));
