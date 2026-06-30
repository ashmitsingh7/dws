"""
permissions.py
==============

Role-Based Access Control for the DWS authentication subsystem.

This module is the single source of truth for:
  - the three default role names (Operator, Supervisor, Administrator)
  - the permission keys those roles map to
  - helpers for checking whether a role/user satisfies a permission

It has no FastAPI dependency and no database connection of its own —
it is pure logic plus the seed data consumed by auth_migration.py.
Routes in auth_api.py and other future API modules import from here
to decide what a logged-in user is allowed to do; they should never
hardcode role names or permission strings directly.

Design notes
------------
Permissions are deliberately coarse-grained (page/feature level, not
per-field) because the spec's three roles map onto whole pages of the
HMI ("Dashboard", "Live Scan", "Transactions", "Reports",
"Diagnostics", "Export", "Settings", user/role management). If a
finer-grained permission is ever needed, add a new key to
PERMISSIONS and extend ROLE_PERMISSIONS — existing callers of
has_permission() do not need to change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# ---------------------------------------------------------------------------
# Role names
# ---------------------------------------------------------------------------
# These three strings are the canonical role names used everywhere:
# database rows, JWT/session claims, and API responses. Do not rename
# without a migration — usernames, audit logs, and any future Electron
# UI will reference these literal strings.

ROLE_OPERATOR = "operator"
ROLE_SUPERVISOR = "supervisor"
ROLE_ADMINISTRATOR = "administrator"

DEFAULT_ROLES = (ROLE_OPERATOR, ROLE_SUPERVISOR, ROLE_ADMINISTRATOR)

# Numeric ranking, mirrors the `level` column seeded in auth_schema.sql.
# Used only for relative comparisons (e.g. "can user A manage user B");
# never exposed as the basis of a permission check on its own — always
# check an explicit permission key instead, so adding a fourth role
# later does not silently change behaviour of existing checks.
ROLE_LEVELS = {
    ROLE_OPERATOR: 10,
    ROLE_SUPERVISOR: 50,
    ROLE_ADMINISTRATOR: 100,
}


# ---------------------------------------------------------------------------
# Permission keys
# ---------------------------------------------------------------------------
# Each key is "<area>.<verb>". Keep verbs limited to: view, run, write,
# manage. "manage" implies create/update/delete for that area.

PERM_DASHBOARD_VIEW = "dashboard.view"
PERM_LIVE_SCAN_VIEW = "live_scan.view"
PERM_TRANSACTIONS_VIEW = "transactions.view"

PERM_REPORTS_VIEW = "reports.view"
PERM_DIAGNOSTICS_VIEW = "diagnostics.view"
PERM_EXPORT_RUN = "export.run"

PERM_SETTINGS_WRITE = "settings.write"
PERM_CALIBRATION_RUN = "calibration.run"
PERM_USERS_MANAGE = "users.manage"
PERM_ROLES_MANAGE = "roles.manage"

ALL_PERMISSIONS = (
    PERM_DASHBOARD_VIEW,
    PERM_LIVE_SCAN_VIEW,
    PERM_TRANSACTIONS_VIEW,
    PERM_REPORTS_VIEW,
    PERM_DIAGNOSTICS_VIEW,
    PERM_EXPORT_RUN,
    PERM_SETTINGS_WRITE,
    PERM_CALIBRATION_RUN,
    PERM_USERS_MANAGE,
    PERM_ROLES_MANAGE,
)

PERMISSION_DESCRIPTIONS = {
    PERM_DASHBOARD_VIEW: "View the main dashboard",
    PERM_LIVE_SCAN_VIEW: "View the live scan / measurement screen",
    PERM_TRANSACTIONS_VIEW: "View transaction history",
    PERM_REPORTS_VIEW: "View reports",
    PERM_DIAGNOSTICS_VIEW: "View diagnostics screens",
    PERM_EXPORT_RUN: "Run CSV/PDF exports",
    PERM_SETTINGS_WRITE: "Change system settings",
    PERM_CALIBRATION_RUN: "Run or override calibration",
    PERM_USERS_MANAGE: "Create, edit, disable, or delete users",
    PERM_ROLES_MANAGE: "View/manage roles and their permissions",
}


# ---------------------------------------------------------------------------
# Role -> permission mapping (per spec section 4)
# ---------------------------------------------------------------------------
# Operator:     Dashboard, Live Scan, Transactions. No settings, no user
#               management, no calibration.
# Supervisor:   Everything Operator can, plus Reports, Diagnostics, Export.
# Administrator: full access (every permission key, including ones added
#               later — see has_permission()).

ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    ROLE_OPERATOR: (
        PERM_DASHBOARD_VIEW,
        PERM_LIVE_SCAN_VIEW,
        PERM_TRANSACTIONS_VIEW,
    ),
    ROLE_SUPERVISOR: (
        PERM_DASHBOARD_VIEW,
        PERM_LIVE_SCAN_VIEW,
        PERM_TRANSACTIONS_VIEW,
        PERM_REPORTS_VIEW,
        PERM_DIAGNOSTICS_VIEW,
        PERM_EXPORT_RUN,
    ),
    ROLE_ADMINISTRATOR: ALL_PERMISSIONS,
}


@dataclass(frozen=True)
class Principal:
    """
    Minimal identity of the currently-authenticated caller, as resolved
    by session_manager.py from a bearer token. Routes receive this via
    a FastAPI dependency rather than reading the DB row directly, so
    permission logic stays in one place.
    """
    user_id: int
    username: str
    role: str
    display_name: str | None = None


def has_permission(role: str, permission: str) -> bool:
    """
    True if the given role name grants the given permission key.

    Administrator is always true for any key in ALL_PERMISSIONS (and,
    as a deliberate safety net, for any *unknown* key too) so a new
    permission introduced by a future feature does not accidentally
    lock out the administrator role until ROLE_PERMISSIONS is updated.
    Operator/Supervisor must be explicitly granted a permission — an
    unknown key for them is "no" by default (fail closed).
    """
    if role == ROLE_ADMINISTRATOR:
        return True
    return permission in ROLE_PERMISSIONS.get(role, ())


def has_any_permission(role: str, permissions: Iterable[str]) -> bool:
    return any(has_permission(role, p) for p in permissions)


def role_level(role: str) -> int:
    """Numeric level for relative comparisons. Unknown roles rank lowest."""
    return ROLE_LEVELS.get(role, 0)


def can_manage_target_role(actor_role: str, target_role: str) -> bool:
    """
    Guardrail used by user_manager.py: a user may only create/edit
    accounts at or below their own level, and only an administrator
    may create another administrator. This prevents a supervisor
    account (if ever granted users.manage in a future customization)
    from minting an administrator account for themselves.
    """
    if actor_role == ROLE_ADMINISTRATOR:
        return True
    return role_level(actor_role) > role_level(target_role)


def is_known_role(role: str) -> bool:
    return role in ROLE_PERMISSIONS
