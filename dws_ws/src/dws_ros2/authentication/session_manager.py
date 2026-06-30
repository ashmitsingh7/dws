"""
session_manager.py
===================

Session lifecycle for the DWS authentication subsystem: issuing,
validating, refreshing, and revoking login sessions.

Storage: the `auth_sessions` table (auth_schema.sql). Deliberately
NOT the same table as the pre-existing `sessions` table created by
database_node.py — that table tracks shift/work-session statistics
(parcel_count, error_count, latency) and is owned by the measurement
pipeline. See the header comment in auth_schema.sql for the full
explanation; this module never reads or writes that table.

Token design
------------
- The bearer token handed to the client is a high-entropy random
  string (secrets.token_urlsafe), generated fresh per login.
- Only its SHA-256 hash is stored in auth_sessions.session_token_hash.
  This mirrors how the password itself is never stored — a stolen
  database backup does not, on its own, yield a usable session.
- Validation re-hashes the presented token and looks up the hash;
  there is no decoding/decrypting step, so there is nothing for an
  attacker to forge offline without already knowing a valid token.

Concurrency / multi-user
-------------------------
Each login creates a new row; the same user can hold multiple
concurrent sessions (e.g. one operator logged in at two stations),
matching the spec's "multiple concurrent users" requirement. Sessions
are independent — revoking one does not affect the others unless the
caller explicitly revokes all sessions for a user (used by
user_manager.py when disabling/deleting an account, and by
password_manager-driven password changes).

Expiry has two independent clocks, both enforced on every validation:
  - absolute lifetime  (`expires_at`)      — SESSION_ABSOLUTE_TTL_SECONDS
  - inactivity timeout (`last_seen_at`)    — SESSION_IDLE_TIMEOUT_SECONDS
Whichever is hit first ends the session. A background sweep
(`purge_expired_sessions`) is provided for periodic cleanup but is not
required for correctness — `validate_session` always re-checks both
clocks itself, so a session past either limit is treated as invalid
even if the sweep hasn't run yet.
"""

from __future__ import annotations

import secrets
import sqlite3
import time
import hashlib
from dataclasses import dataclass

from permissions import Principal

# Tunables. A real deployment can override these via auth_manager.py's
# config plumbing (see AUTHENTICATION_ARCHITECTURE.md) — kept as
# module constants here so this file has no FastAPI/config_loader
# dependency and stays easy to unit test in isolation.
SESSION_ABSOLUTE_TTL_SECONDS = 12 * 60 * 60   # 12 hours — covers a full shift
SESSION_IDLE_TIMEOUT_SECONDS = 30 * 60        # 30 minutes of inactivity
SESSION_TOKEN_BYTES = 32


class SessionError(Exception):
    """Base class for session-related failures."""


class SessionExpired(SessionError):
    pass


class SessionInvalid(SessionError):
    pass


@dataclass(frozen=True)
class SessionInfo:
    id: int
    user_id: int
    username: str
    role: str
    display_name: str | None
    station_id: str | None
    ip_address: str | None
    created_at: float
    last_seen_at: float
    expires_at: float


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_session_token() -> str:
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


class SessionManager:
    """
    Wraps a sqlite3.Connection (caller-owned, matching the rest of
    the DWS codebase's pattern of one shared connection per process
    rather than a connection pool — see api_server.py's get_conn()
    and DatabaseNode._open_db()). Every public method opens its own
    cursor and commits immediately; no long-lived transactions are
    held, so this is safe to call from multiple request-handling
    threads/coroutines against the same WAL-mode connection.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    # ------------------------------------------------------------
    # Create
    # ------------------------------------------------------------

    def create_session(
        self,
        *,
        user_id: int,
        username: str,
        role: str,
        display_name: str | None = None,
        station_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[str, SessionInfo]:
        """
        Issue a new session for an already-authenticated user.
        Returns (raw_token, SessionInfo). raw_token is shown to the
        caller exactly once — only its hash is persisted.
        """
        raw_token = generate_session_token()
        token_hash = _hash_token(raw_token)
        now = time.time()
        expires_at = now + SESSION_ABSOLUTE_TTL_SECONDS

        cur = self._conn.execute(
            """
            INSERT INTO auth_sessions
                (session_token_hash, user_id, station_id, ip_address,
                 user_agent, created_at, last_seen_at, expires_at, revoked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (token_hash, user_id, station_id, ip_address, user_agent, now, now, expires_at),
        )
        self._conn.commit()

        info = SessionInfo(
            id=cur.lastrowid,
            user_id=user_id,
            username=username,
            role=role,
            display_name=display_name,
            station_id=station_id,
            ip_address=ip_address,
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
        )
        return raw_token, info

    # ------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------

    def validate_session(self, raw_token: str, *, touch: bool = True) -> Principal:
        """
        Validate a bearer token. Raises SessionInvalid (unknown,
        revoked, or malformed token) or SessionExpired (idle timeout
        or absolute TTL exceeded). On success, returns a Principal
        and — unless touch=False — refreshes last_seen_at so the
        idle timer resets, matching normal "any request keeps you
        logged in" UX.

        touch=False exists for read-only diagnostic calls (e.g. a
        health-check style "is this token currently valid" probe)
        that should not themselves count as activity; ordinary API
        routes should use the default touch=True.
        """
        if not raw_token:
            raise SessionInvalid("No session token provided.")

        token_hash = _hash_token(raw_token)
        row = self._conn.execute(
            """
            SELECT s.id, s.user_id, s.station_id, s.ip_address,
                   s.created_at, s.last_seen_at, s.expires_at, s.revoked_at,
                   u.username, u.display_name, u.is_active, r.name AS role_name
            FROM auth_sessions s
            JOIN auth_users u ON u.id = s.user_id
            JOIN roles r ON r.id = u.role_id
            WHERE s.session_token_hash = ?
            """,
            (token_hash,),
        ).fetchone()

        if row is None:
            raise SessionInvalid("Session not found.")

        if row["revoked_at"] is not None:
            raise SessionInvalid("Session has been revoked.")

        if not row["is_active"]:
            raise SessionInvalid("User account is disabled.")

        now = time.time()

        if now >= row["expires_at"]:
            raise SessionExpired("Session has exceeded its maximum lifetime.")

        if now - row["last_seen_at"] >= SESSION_IDLE_TIMEOUT_SECONDS:
            raise SessionExpired("Session timed out due to inactivity.")

        if touch:
            self._conn.execute(
                "UPDATE auth_sessions SET last_seen_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            self._conn.commit()

        return Principal(
            user_id=row["user_id"],
            username=row["username"],
            role=row["role_name"],
            display_name=row["display_name"],
        )

    # ------------------------------------------------------------
    # Revoke / logout
    # ------------------------------------------------------------

    def revoke_session(self, raw_token: str) -> bool:
        """Logout. Idempotent — revoking an already-revoked/unknown
        token returns False rather than raising, since "logout" on a
        stale token should not surface as an error to the client."""
        token_hash = _hash_token(raw_token)
        now = time.time()
        cur = self._conn.execute(
            """
            UPDATE auth_sessions
            SET revoked_at = ?
            WHERE session_token_hash = ? AND revoked_at IS NULL
            """,
            (now, token_hash),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def revoke_all_sessions_for_user(self, user_id: int) -> int:
        """
        Used by user_manager.py on disable/delete/password-change,
        and by password_manager-driven resets, to force re-login
        everywhere immediately. Returns count of sessions revoked.
        """
        now = time.time()
        cur = self._conn.execute(
            """
            UPDATE auth_sessions
            SET revoked_at = ?
            WHERE user_id = ? AND revoked_at IS NULL
            """,
            (now, user_id),
        )
        self._conn.commit()
        return cur.rowcount

    # ------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------

    def list_active_sessions(self, user_id: int | None = None) -> list[SessionInfo]:
        """
        All currently-valid (not revoked, not expired by either
        clock) sessions, optionally filtered to one user. Useful for
        an admin "active sessions" view and for tests.
        """
        now = time.time()
        idle_cutoff = now - SESSION_IDLE_TIMEOUT_SECONDS

        query = """
            SELECT s.id, s.user_id, s.station_id, s.ip_address,
                   s.created_at, s.last_seen_at, s.expires_at,
                   u.username, u.display_name, r.name AS role_name
            FROM auth_sessions s
            JOIN auth_users u ON u.id = s.user_id
            JOIN roles r ON r.id = u.role_id
            WHERE s.revoked_at IS NULL
              AND s.expires_at > ?
              AND s.last_seen_at > ?
        """
        params: list = [now, idle_cutoff]
        if user_id is not None:
            query += " AND s.user_id = ?"
            params.append(user_id)
        query += " ORDER BY s.last_seen_at DESC"

        rows = self._conn.execute(query, params).fetchall()
        return [
            SessionInfo(
                id=row["id"],
                user_id=row["user_id"],
                username=row["username"],
                role=row["role_name"],
                display_name=row["display_name"],
                station_id=row["station_id"],
                ip_address=row["ip_address"],
                created_at=row["created_at"],
                last_seen_at=row["last_seen_at"],
                expires_at=row["expires_at"],
            )
            for row in rows
        ]

    # ------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------

    def purge_expired_sessions(self, *, older_than_days: int = 30) -> int:
        """
        Housekeeping only — deletes session rows that are both
        inactive (revoked or expired by either clock) AND older than
        `older_than_days`, so recently-expired sessions stick around
        briefly for audit/debugging before being swept. validate_session
        never depends on this having run. Safe to call from a periodic
        background thread (e.g. alongside health_monitor.py's existing
        daemon-thread pattern) or a cron-style call from auth_manager.py.
        Returns number of rows deleted.
        """
        now = time.time()
        cutoff = now - (older_than_days * 86400)
        idle_cutoff = now - SESSION_IDLE_TIMEOUT_SECONDS

        cur = self._conn.execute(
            """
            DELETE FROM auth_sessions
            WHERE created_at < ?
              AND (
                    revoked_at IS NOT NULL
                    OR expires_at <= ?
                    OR last_seen_at <= ?
                  )
            """,
            (cutoff, now, idle_cutoff),
        )
        self._conn.commit()
        return cur.rowcount
