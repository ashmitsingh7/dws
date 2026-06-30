"""
auth_manager.py
================

Orchestrates the login/logout flow and owns the policy decisions
(lockout threshold, lockout duration, rate limiting) that
user_manager.py and session_manager.py expose as primitives but do
not decide on their own.

This is the module auth_api.py talks to directly. It is the only
module in this package that writes to `auth_login_history`, so every
authentication-relevant event is guaranteed to be audited from one
place rather than scattered across the call graph.

Rate limiting note
-------------------
Account lockout (per-username, persisted in auth_users) handles the
"repeated failures against one account" case durably and survives a
process restart. A lightweight in-memory IP rate limiter is layered
on top to slow down breadth-first attacks that spray many different
usernames from one source — this one is intentionally NOT persisted
to the database (it would add write load to every single login
attempt, including legitimate ones, for a signal that is only useful
within a short rolling window) and resets if the process restarts.
That's an acceptable tradeoff: the durable per-account lockout is the
real defense; the IP limiter is a cheap speed bump on top of it.
"""

from __future__ import annotations

import sqlite3
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from collections import defaultdict, deque

from permissions import Principal
from password_manager import (
    verify_password,
    needs_rehash,
    hash_password,
    PasswordPolicyError,
)
from user_manager import (
    UserManager,
    UserRecord,
    UserNotFoundError,
)
from session_manager import (
    SessionManager,
    SessionInfo,
    SessionExpired,
    SessionInvalid,
)


# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 15 * 60          # 15 minutes

# IP-based throttle: more than this many failed attempts from one IP
# within the rolling window triggers a temporary soft block, independent
# of which username(s) were targeted.
IP_RATE_LIMIT_MAX_ATTEMPTS = 20
IP_RATE_LIMIT_WINDOW_SECONDS = 5 * 60
IP_RATE_LIMIT_BLOCK_SECONDS = 5 * 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AuthError(Exception):
    """Base class for authentication failures surfaced to auth_api.py."""


class InvalidCredentialsError(AuthError):
    pass


class AccountDisabledError(AuthError):
    pass


class AccountLockedError(AuthError):
    def __init__(self, message: str, retry_after_seconds: int):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class RateLimitedError(AuthError):
    def __init__(self, message: str, retry_after_seconds: int):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class LoginResult:
    token: str
    session: SessionInfo
    must_change_password: bool


class _IpRateLimiter:
    """Process-local sliding-window counter. See module docstring for why
    this is intentionally not persisted to the database."""

    def __init__(self, max_attempts: int, window_seconds: int, block_seconds: int):
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._block_seconds = block_seconds
        self._attempts: dict[str, deque] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, ip: str | None) -> int | None:
        """Returns seconds-until-retry if currently blocked, else None."""
        if not ip:
            return None
        with self._lock:
            blocked_until = self._blocked_until.get(ip)
            if blocked_until and blocked_until > time.time():
                return int(blocked_until - time.time())
            return None

    def record_failure(self, ip: str | None) -> None:
        if not ip:
            return
        now = time.time()
        with self._lock:
            window = self._attempts[ip]
            window.append(now)
            cutoff = now - self._window_seconds
            while window and window[0] < cutoff:
                window.popleft()
            if len(window) >= self._max_attempts:
                self._blocked_until[ip] = now + self._block_seconds

    def record_success(self, ip: str | None) -> None:
        if not ip:
            return
        with self._lock:
            self._attempts.pop(ip, None)
            self._blocked_until.pop(ip, None)


class AuthManager:
    """
    Top-level façade used by auth_api.py. Holds one UserManager and
    one SessionManager over a shared connection, plus the IP rate
    limiter (which is per-process, not per-connection — instantiate
    one AuthManager per process and reuse it across requests, not
    one per request).
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self.users = UserManager(conn)
        self.sessions = SessionManager(conn)
        self._ip_limiter = _IpRateLimiter(
            IP_RATE_LIMIT_MAX_ATTEMPTS,
            IP_RATE_LIMIT_WINDOW_SECONDS,
            IP_RATE_LIMIT_BLOCK_SECONDS,
        )

    # ------------------------------------------------------------
    # Audit logging (the only place in this package that writes
    # to auth_login_history)
    # ------------------------------------------------------------

    def _log(
        self,
        *,
        username: str,
        action: str,
        result: str,
        ip_address: str | None,
        user_id: int | None = None,
        detail: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO auth_login_history
                (timestamp, username, user_id, ip_address, action, result, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (_now_iso(), username, user_id, ip_address, action, result, detail),
        )
        self._conn.commit()

    # ------------------------------------------------------------
    # Login
    # ------------------------------------------------------------

    def login(
        self,
        username: str,
        password: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
        station_id: str | None = None,
    ) -> LoginResult:
        username = (username or "").strip()

        retry_after = self._ip_limiter.check(ip_address)
        if retry_after is not None:
            self._log(
                username=username, action="login_failed", result="failure",
                ip_address=ip_address, detail="ip_rate_limited",
            )
            raise RateLimitedError(
                "Too many login attempts from this address. Try again shortly.",
                retry_after,
            )

        try:
            user = self.users.get_user_by_username(username)
        except UserNotFoundError:
            user = None

        # Always run a bcrypt verification, even when the username
        # doesn't exist, against a fixed dummy hash. This keeps the
        # response-time profile for "unknown username" and "wrong
        # password" close to identical, closing an easy timing-based
        # username-enumeration side channel without needing a more
        # invasive constant-time framework around the whole request.
        stored_hash = self.users.get_password_hash(username) or _DUMMY_HASH
        password_ok = verify_password(password, stored_hash)

        if user is None or not password_ok:
            self._ip_limiter.record_failure(ip_address)
            if user is not None:
                new_count = self.users.record_failed_attempt(username)
                if new_count >= MAX_FAILED_ATTEMPTS:
                    self.users.lock_account(user.id, duration_seconds=LOCKOUT_DURATION_SECONDS)
                    self._log(
                        username=username, action="account_locked", result="success",
                        ip_address=ip_address, user_id=user.id,
                        detail=f"{new_count} consecutive failed attempts",
                    )
            self._log(
                username=username, action="login_failed", result="failure",
                ip_address=ip_address, user_id=(user.id if user else None),
                detail="invalid credentials",
            )
            raise InvalidCredentialsError("Invalid username or password.")

        if not user.is_active:
            self._log(
                username=username, action="login_failed", result="failure",
                ip_address=ip_address, user_id=user.id, detail="account disabled",
            )
            raise AccountDisabledError("This account has been disabled.")

        if self.users.is_locked(user):
            remaining = int(user.locked_until - time.time())
            self._log(
                username=username, action="login_failed", result="failure",
                ip_address=ip_address, user_id=user.id, detail="account locked",
            )
            raise AccountLockedError(
                "Account is temporarily locked due to repeated failed logins.",
                max(remaining, 1),
            )

        # Success.
        self._ip_limiter.record_success(ip_address)
        self.users.record_login_success(user.id)

        if needs_rehash(stored_hash):
            self._conn.execute(
                "UPDATE auth_users SET password_hash = ? WHERE id = ?",
                (hash_password(password), user.id),
            )
            self._conn.commit()

        token, session = self.sessions.create_session(
            user_id=user.id,
            username=user.username,
            role=user.role,
            display_name=user.display_name,
            station_id=station_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        self._log(
            username=username, action="login_success", result="success",
            ip_address=ip_address, user_id=user.id, detail=station_id,
        )

        return LoginResult(
            token=token,
            session=session,
            must_change_password=user.must_change_password,
        )

    # ------------------------------------------------------------
    # Logout
    # ------------------------------------------------------------

    def logout(self, raw_token: str, *, ip_address: str | None = None) -> None:
        try:
            principal = self.sessions.validate_session(raw_token, touch=False)
            username = principal.username
            user_id = principal.user_id
        except (SessionInvalid, SessionExpired):
            username, user_id = "(unknown)", None

        revoked = self.sessions.revoke_session(raw_token)
        self._log(
            username=username, action="logout",
            result="success" if revoked else "failure",
            ip_address=ip_address, user_id=user_id,
        )

    # ------------------------------------------------------------
    # Session lookup (used by auth_api.py dependency + other routers)
    # ------------------------------------------------------------

    def get_principal(self, raw_token: str) -> Principal:
        """Raises SessionInvalid / SessionExpired on failure — callers
        in auth_api.py translate those into HTTP 401."""
        return self.sessions.validate_session(raw_token)

    # ------------------------------------------------------------
    # Password change (self-service, requires current password)
    # ------------------------------------------------------------

    def change_own_password(
        self,
        user_id: int,
        current_password: str,
        new_password: str,
        *,
        ip_address: str | None = None,
    ) -> None:
        user = self.users.get_user(user_id)
        stored_hash = self.users.get_password_hash(user.username)
        if not verify_password(current_password, stored_hash or ""):
            self._log(
                username=user.username, action="password_change", result="failure",
                ip_address=ip_address, user_id=user_id, detail="current password mismatch",
            )
            raise InvalidCredentialsError("Current password is incorrect.")

        try:
            self.users.change_password(user_id, new_password)
        except PasswordPolicyError as exc:
            self._log(
                username=user.username, action="password_change", result="failure",
                ip_address=ip_address, user_id=user_id, detail=str(exc),
            )
            raise

        self.sessions.revoke_all_sessions_for_user(user_id)
        self._log(
            username=user.username, action="password_change", result="success",
            ip_address=ip_address, user_id=user_id,
        )

    # ------------------------------------------------------------
    # Password reset (admin-initiated, no current password needed)
    # ------------------------------------------------------------

    def admin_reset_password(
        self,
        *,
        actor_username: str,
        target_user_id: int,
        new_password: str,
        ip_address: str | None = None,
    ) -> None:
        target = self.users.get_user(target_user_id)
        self.users.change_password(target_user_id, new_password, must_change_password=True)
        self.sessions.revoke_all_sessions_for_user(target_user_id)
        self._log(
            username=actor_username, action="password_reset", result="success",
            ip_address=ip_address, user_id=target_user_id,
            detail=f"reset by {actor_username} for {target.username}",
        )

    # ------------------------------------------------------------
    # Audit logging for admin-initiated user-management actions.
    # These are the public entry points auth_api.py should call —
    # _log() itself stays private so every audited action funnels
    # through a named, intention-revealing method instead of letting
    # callers pass an arbitrary free-text `action` string.
    # ------------------------------------------------------------

    def log_user_created(self, *, actor_username: str, target_username: str,
                          target_role: str, ip_address: str | None = None) -> None:
        self._log(
            username=actor_username, action="user_created", result="success",
            ip_address=ip_address,
            detail=f"created {target_username} ({target_role})",
        )

    def log_user_deleted(self, *, actor_username: str, target_username: str,
                          ip_address: str | None = None) -> None:
        self._log(
            username=actor_username, action="user_deleted", result="success",
            ip_address=ip_address,
            detail=f"deleted {target_username}",
        )

    def log_role_changed(self, *, actor_username: str, target_user_id: int,
                          new_role: str, ip_address: str | None = None) -> None:
        self._log(
            username=actor_username, action="role_changed", result="success",
            ip_address=ip_address, user_id=target_user_id,
            detail=f"role -> {new_role}",
        )

    def log_account_disabled(self, *, actor_username: str, target_user_id: int,
                              ip_address: str | None = None) -> None:
        self._log(
            username=actor_username, action="disabled_account", result="success",
            ip_address=ip_address, user_id=target_user_id,
        )

    def log_account_enabled(self, *, actor_username: str, target_user_id: int,
                             ip_address: str | None = None) -> None:
        self._log(
            username=actor_username, action="enabled_account", result="success",
            ip_address=ip_address, user_id=target_user_id,
        )


# A fixed, valid bcrypt hash of a value nobody will ever type, used
# only as the comparison target when a username lookup misses, so
# verify_password() always does real bcrypt work and the timing
# profile for "no such user" matches "wrong password" for a real
# user. Generated once (bcrypt.hashpw of an arbitrary, unused string
# at the same cost factor as BCRYPT_ROUNDS) and frozen as a constant
# — it never needs to change, and the plaintext behind it is
# irrelevant and intentionally not recorded anywhere.
_DUMMY_HASH = "$2b$12$FGh/XND7ycN2Ds5v6oV.RusgmgIehsB5CKFollncH3gegSzKMssiG"
