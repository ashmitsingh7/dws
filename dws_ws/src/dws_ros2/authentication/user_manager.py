"""
user_manager.py
================

User account management: create, list, update, disable/enable,
delete, change password, and assign roles.

This module owns all writes to `auth_users`. It does not write to
`auth_login_history` directly — callers (auth_api.py, via
auth_manager.py) are responsible for logging the audit event, because
only they know request-level context (IP address) that belongs in
the audit row. Keeping audit writes at the call site instead of
burying them here also means a unit test for, say, disable_user()
doesn't need to thread through a logger object.

Account lockout state (failed_attempts, locked_until) is read/written
here, but the *decision* to lock an account after N failures lives in
auth_manager.py (which owns the login flow and therefore knows the
configured threshold). This module just exposes the primitives:
record_failed_attempt(), reset_failed_attempts(), lock_account(),
is_locked().
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from permissions import (
    DEFAULT_ROLES,
    can_manage_target_role,
    is_known_role,
)
from password_manager import (
    hash_password,
    assert_password_complexity,
    PasswordPolicyError,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class UserManagerError(Exception):
    """Base class for user-management failures."""


class UsernameTakenError(UserManagerError):
    pass


class UserNotFoundError(UserManagerError):
    pass


class UnknownRoleError(UserManagerError):
    pass


class InsufficientPrivilegeError(UserManagerError):
    """Raised when an actor tries to manage a user/role above their level."""


class LastAdministratorError(UserManagerError):
    """
    Raised when an operation (disable, delete, demote) would leave
    the system with zero enabled administrator accounts. A warehouse
    floor system with no path back to an admin account is a worse
    failure mode than a slightly annoying guardrail, so this is
    enforced unconditionally rather than as a configurable option.
    """


@dataclass(frozen=True)
class UserRecord:
    id: int
    username: str
    display_name: str | None
    role: str
    is_active: bool
    must_change_password: bool
    failed_attempts: int
    locked_until: float | None
    created_at: str
    created_by: str | None
    last_login_at: str | None


class UserManager:
    """Wraps a sqlite3.Connection, same ownership pattern as SessionManager."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    # ------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------

    def _role_id(self, role_name: str) -> int:
        if not is_known_role(role_name):
            raise UnknownRoleError(f"Unknown role: {role_name!r}")
        row = self._conn.execute(
            "SELECT id FROM roles WHERE name = ?", (role_name,)
        ).fetchone()
        if row is None:
            raise UnknownRoleError(
                f"Role {role_name!r} is not seeded in the roles table. "
                "Run auth_migration.py."
            )
        return row["id"]

    def _row_to_record(self, row: sqlite3.Row) -> UserRecord:
        return UserRecord(
            id=row["id"],
            username=row["username"],
            display_name=row["display_name"],
            role=row["role_name"],
            is_active=bool(row["is_active"]),
            must_change_password=bool(row["must_change_password"]),
            failed_attempts=row["failed_attempts"],
            locked_until=row["locked_until"],
            created_at=row["created_at"],
            created_by=row["created_by"],
            last_login_at=row["last_login_at"],
        )

    _SELECT_BASE = """
        SELECT u.id, u.username, u.display_name, u.password_hash,
               u.is_active, u.must_change_password, u.failed_attempts,
               u.locked_until, u.created_at, u.created_by, u.last_login_at,
               r.name AS role_name
        FROM auth_users u
        JOIN roles r ON r.id = u.role_id
    """

    def _count_enabled_admins(self, exclude_user_id: int | None = None) -> int:
        query = (
            "SELECT COUNT(*) AS n FROM auth_users u "
            "JOIN roles r ON r.id = u.role_id "
            "WHERE r.name = 'administrator' AND u.is_active = 1"
        )
        params: list = []
        if exclude_user_id is not None:
            query += " AND u.id != ?"
            params.append(exclude_user_id)
        return self._conn.execute(query, params).fetchone()["n"]

    # ------------------------------------------------------------
    # Create
    # ------------------------------------------------------------

    def create_user(
        self,
        *,
        username: str,
        password: str,
        role: str,
        display_name: str | None = None,
        created_by: str | None = None,
        must_change_password: bool = True,
        actor_role: str | None = None,
    ) -> UserRecord:
        """
        Create a new account. `actor_role`, if given, is checked
        against `role` via can_manage_target_role() so (for example)
        a future non-administrator with a hypothetical users.manage
        grant cannot mint an administrator account. When actor_role
        is None (e.g. the bootstrap script creating the seed admin),
        no privilege check is performed.
        """
        username = username.strip()
        if not username:
            raise UserManagerError("Username must not be empty.")
        if not is_known_role(role):
            raise UnknownRoleError(f"Unknown role: {role!r}")
        if actor_role is not None and not can_manage_target_role(actor_role, role):
            raise InsufficientPrivilegeError(
                f"Role {actor_role!r} cannot create a {role!r} account."
            )

        assert_password_complexity(password, username=username)

        existing = self._conn.execute(
            "SELECT 1 FROM auth_users WHERE username = ?", (username,)
        ).fetchone()
        if existing is not None:
            raise UsernameTakenError(f"Username {username!r} is already in use.")

        role_id = self._role_id(role)
        now = _now_iso()
        pw_hash = hash_password(password)

        cur = self._conn.execute(
            """
            INSERT INTO auth_users
                (username, display_name, password_hash, role_id, is_active,
                 must_change_password, password_changed_at, created_at, created_by)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (username, display_name, pw_hash, role_id,
             int(must_change_password), now, now, created_by),
        )
        self._conn.commit()
        return self.get_user(cur.lastrowid)

    # ------------------------------------------------------------
    # Read
    # ------------------------------------------------------------

    def get_user(self, user_id: int) -> UserRecord:
        row = self._conn.execute(
            self._SELECT_BASE + " WHERE u.id = ?", (user_id,)
        ).fetchone()
        if row is None:
            raise UserNotFoundError(f"No user with id {user_id}")
        return self._row_to_record(row)

    def get_user_by_username(self, username: str) -> UserRecord:
        row = self._conn.execute(
            self._SELECT_BASE + " WHERE u.username = ?", (username,)
        ).fetchone()
        if row is None:
            raise UserNotFoundError(f"No user with username {username!r}")
        return self._row_to_record(row)

    def get_password_hash(self, username: str) -> str | None:
        """Used only by auth_manager.py during login verification."""
        row = self._conn.execute(
            "SELECT password_hash FROM auth_users WHERE username = ?",
            (username,),
        ).fetchone()
        return row["password_hash"] if row else None

    def list_users(self) -> list[UserRecord]:
        rows = self._conn.execute(
            self._SELECT_BASE + " ORDER BY u.username"
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    # ------------------------------------------------------------
    # Update
    # ------------------------------------------------------------

    def update_user(
        self,
        user_id: int,
        *,
        display_name: str | None = None,
        role: str | None = None,
        actor_role: str | None = None,
    ) -> UserRecord:
        current = self.get_user(user_id)

        if role is not None:
            if not is_known_role(role):
                raise UnknownRoleError(f"Unknown role: {role!r}")
            if actor_role is not None and not can_manage_target_role(actor_role, role):
                raise InsufficientPrivilegeError(
                    f"Role {actor_role!r} cannot assign role {role!r}."
                )
            if current.role == "administrator" and role != "administrator":
                if self._count_enabled_admins(exclude_user_id=user_id) == 0:
                    raise LastAdministratorError(
                        "Cannot demote the last enabled administrator."
                    )

        fields, params = [], []
        if display_name is not None:
            fields.append("display_name = ?")
            params.append(display_name)
        if role is not None:
            fields.append("role_id = ?")
            params.append(self._role_id(role))
        if fields:
            fields.append("updated_at = ?")
            params.append(_now_iso())
            params.append(user_id)
            self._conn.execute(
                f"UPDATE auth_users SET {', '.join(fields)} WHERE id = ?",
                params,
            )
            self._conn.commit()

        return self.get_user(user_id)

    def change_password(
        self,
        user_id: int,
        new_password: str,
        *,
        must_change_password: bool = False,
    ) -> None:
        user = self.get_user(user_id)
        assert_password_complexity(new_password, username=user.username)
        pw_hash = hash_password(new_password)
        now = _now_iso()
        self._conn.execute(
            """
            UPDATE auth_users
            SET password_hash = ?, password_changed_at = ?, updated_at = ?,
                must_change_password = ?, failed_attempts = 0, locked_until = NULL
            WHERE id = ?
            """,
            (pw_hash, now, now, int(must_change_password), user_id),
        )
        self._conn.commit()

    def set_must_change_password(self, user_id: int, value: bool) -> None:
        self._conn.execute(
            "UPDATE auth_users SET must_change_password = ?, updated_at = ? WHERE id = ?",
            (int(value), _now_iso(), user_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------
    # Enable / disable / delete
    # ------------------------------------------------------------

    def disable_user(self, user_id: int) -> None:
        user = self.get_user(user_id)
        if user.role == "administrator" and self._count_enabled_admins(exclude_user_id=user_id) == 0:
            raise LastAdministratorError("Cannot disable the last enabled administrator.")
        self._conn.execute(
            "UPDATE auth_users SET is_active = 0, updated_at = ? WHERE id = ?",
            (_now_iso(), user_id),
        )
        self._conn.commit()

    def enable_user(self, user_id: int) -> None:
        self._conn.execute(
            """
            UPDATE auth_users
            SET is_active = 1, failed_attempts = 0, locked_until = NULL, updated_at = ?
            WHERE id = ?
            """,
            (_now_iso(), user_id),
        )
        self._conn.commit()

    def delete_user(self, user_id: int) -> None:
        """
        Hard delete. Auth sessions cascade-delete via the foreign key
        (ON DELETE CASCADE on auth_sessions.user_id). Login history
        rows are preserved — user_id is set NULL via ON DELETE SET
        NULL so the audit trail survives account deletion, with the
        username string in auth_login_history.username remaining as
        the historical record.
        """
        user = self.get_user(user_id)
        if user.role == "administrator" and self._count_enabled_admins(exclude_user_id=user_id) == 0:
            raise LastAdministratorError("Cannot delete the last enabled administrator.")
        self._conn.execute("DELETE FROM auth_users WHERE id = ?", (user_id,))
        self._conn.commit()

    # ------------------------------------------------------------
    # Lockout primitives (decision logic lives in auth_manager.py)
    # ------------------------------------------------------------

    def record_failed_attempt(self, username: str) -> int:
        """Increments failed_attempts for an existing user and returns
        the new count. No-op (returns 0) if the username doesn't exist —
        callers must not use this to distinguish valid from invalid
        usernames (that would be a username-enumeration side channel)."""
        row = self._conn.execute(
            "SELECT id, failed_attempts FROM auth_users WHERE username = ?",
            (username,),
        ).fetchone()
        if row is None:
            return 0
        new_count = row["failed_attempts"] + 1
        self._conn.execute(
            "UPDATE auth_users SET failed_attempts = ? WHERE id = ?",
            (new_count, row["id"]),
        )
        self._conn.commit()
        return new_count

    def reset_failed_attempts(self, user_id: int) -> None:
        self._conn.execute(
            "UPDATE auth_users SET failed_attempts = 0, locked_until = NULL WHERE id = ?",
            (user_id,),
        )
        self._conn.commit()

    def lock_account(self, user_id: int, *, duration_seconds: int) -> float:
        """Sets locked_until to now + duration_seconds. Returns that timestamp."""
        until = time.time() + duration_seconds
        self._conn.execute(
            "UPDATE auth_users SET locked_until = ? WHERE id = ?",
            (until, user_id),
        )
        self._conn.commit()
        return until

    def is_locked(self, user: UserRecord) -> bool:
        return user.locked_until is not None and user.locked_until > time.time()

    def record_login_success(self, user_id: int) -> None:
        now = _now_iso()
        self._conn.execute(
            """
            UPDATE auth_users
            SET last_login_at = ?, failed_attempts = 0, locked_until = NULL
            WHERE id = ?
            """,
            (now, user_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------
    # Password expiration support
    # ------------------------------------------------------------

    def set_password_expiry(self, user_id: int, *, days_from_now: int | None) -> None:
        """
        days_from_now=None clears any expiry (password never expires).
        Called by auth_manager.py if a password-expiration policy is
        configured (see AUTHENTICATION_ARCHITECTURE.md, "Password
        Expiration" section) — disabled by default for this spec's
        warehouse-floor deployment, but the primitive is here so a
        future policy change doesn't need a schema migration.
        """
        if days_from_now is None:
            self._conn.execute(
                "UPDATE auth_users SET password_expires_at = NULL WHERE id = ?",
                (user_id,),
            )
        else:
            expiry = (datetime.now(timezone.utc) + timedelta(days=days_from_now)).isoformat(timespec="seconds")
            self._conn.execute(
                "UPDATE auth_users SET password_expires_at = ? WHERE id = ?",
                (expiry, user_id),
            )
        self._conn.commit()

    def is_password_expired(self, user: "UserRecord") -> bool:
        row = self._conn.execute(
            "SELECT password_expires_at FROM auth_users WHERE id = ?",
            (user.id,),
        ).fetchone()
        if row is None or row["password_expires_at"] is None:
            return False
        try:
            expiry = datetime.fromisoformat(row["password_expires_at"])
        except ValueError:
            return False
        now = datetime.now(timezone.utc) if expiry.tzinfo else datetime.utcnow()
        return now > expiry
