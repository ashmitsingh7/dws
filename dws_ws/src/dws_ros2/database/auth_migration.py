"""
auth_migration.py
==================

Applies auth_schema.sql to the DWS database and seeds:
  - the `permissions` table (from permissions.ALL_PERMISSIONS)
  - the `role_permissions` mapping (from permissions.ROLE_PERMISSIONS)
  - a bootstrap administrator account, ONLY if no administrator
    account exists yet.

Safe to run multiple times — every step here is idempotent:
  - auth_schema.sql uses CREATE TABLE/INDEX IF NOT EXISTS
  - permission/role_permission seeding uses INSERT OR IGNORE
  - the bootstrap admin is only created when zero administrator
    rows exist, so re-running this after an admin has changed their
    password or been renamed does not reset anything.

Usage
-----
    python3 -m database.auth_migration

or, programmatically:

    from database.auth_migration import run_migration
    run_migration(db_path)

This module deliberately does NOT import api_server.py, FastAPI, or
anything Electron/UI related — it only needs a path to the SQLite
file, resolved the same way the rest of the codebase resolves it
(dws_ros2.paths.DB_PATH), so it can be invoked as part of normal
backend startup (a one-line call from run_dws.py or api_server.py's
startup, wherever Agent 1/2's integration point lands — see
AUTHENTICATION_ARCHITECTURE.md) or run standalone from the CLI.
"""

from __future__ import annotations

import os
import sys
import sqlite3
import secrets
import string
import argparse

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_AUTH_DIR = os.path.join(os.path.dirname(_THIS_DIR), "authentication")
for _p in (_THIS_DIR, _AUTH_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from permissions import (  # noqa: E402
    ALL_PERMISSIONS,
    PERMISSION_DESCRIPTIONS,
    ROLE_PERMISSIONS,
    DEFAULT_ROLES,
)
from password_manager import hash_password  # noqa: E402


SCHEMA_FILENAME = "auth_schema.sql"


def _load_schema_sql() -> str:
    schema_path = os.path.join(_THIS_DIR, SCHEMA_FILENAME)
    with open(schema_path, "r", encoding="utf-8") as fh:
        return fh.read()


def _resolve_db_path(explicit_path: str | None) -> str:
    if explicit_path:
        return explicit_path
    try:
        # Prefer the project's own resolver so this migration always
        # targets the same database file as api_server.py / database_node.py,
        # regardless of source-checkout vs. colcon-install layout.
        from dws_ros2.paths import DB_PATH  # type: ignore
        return DB_PATH
    except ImportError:
        env_override = os.environ.get("DWS_DB_PATH")
        if env_override:
            return env_override
        raise RuntimeError(
            "Could not resolve the DWS database path: dws_ros2.paths is not "
            "importable and DWS_DB_PATH is not set. Pass --db-path explicitly."
        )


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_load_schema_sql())
    conn.commit()


def seed_permissions(conn: sqlite3.Connection) -> None:
    for key in ALL_PERMISSIONS:
        conn.execute(
            "INSERT OR IGNORE INTO permissions (key, description) VALUES (?, ?)",
            (key, PERMISSION_DESCRIPTIONS.get(key, "")),
        )
    conn.commit()


def seed_role_permissions(conn: sqlite3.Connection) -> None:
    role_rows = {
        row["name"]: row["id"]
        for row in conn.execute("SELECT id, name FROM roles").fetchall()
    }
    perm_rows = {
        row["key"]: row["id"]
        for row in conn.execute("SELECT id, key FROM permissions").fetchall()
    }

    for role_name, perm_keys in ROLE_PERMISSIONS.items():
        role_id = role_rows.get(role_name)
        if role_id is None:
            continue  # roles table not seeded yet — schema apply order issue
        for perm_key in perm_keys:
            perm_id = perm_rows.get(perm_key)
            if perm_id is None:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
                (role_id, perm_id),
            )
    conn.commit()


def _generate_temp_password(length: int = 16) -> str:
    """
    Generates a random password guaranteed to satisfy
    password_manager.validate_password_complexity (letters + digits,
    length >= MIN_PASSWORD_LENGTH). Uses secrets (CSPRNG), not random.
    """
    alphabet = string.ascii_letters + string.digits
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.isdigit() for c in candidate) and any(c.isalpha() for c in candidate):
            return candidate


def ensure_bootstrap_admin(
    conn: sqlite3.Connection,
    *,
    username: str = "admin",
) -> str | None:
    """
    Creates a default administrator account with a freshly generated
    random password IF AND ONLY IF zero administrator accounts exist
    yet. Returns the plaintext temporary password if one was created
    (so the caller — typically a CLI run of this script — can print
    it exactly once), or None if an administrator already existed and
    nothing was created.

    The generated password is never written anywhere except into the
    bcrypt hash; it is returned to the caller in-memory only.
    must_change_password is forced True so this temporary password
    cannot become a permanent one by accident.
    """
    existing_admin_count = conn.execute(
        """
        SELECT COUNT(*) AS n FROM auth_users u
        JOIN roles r ON r.id = u.role_id
        WHERE r.name = 'administrator'
        """
    ).fetchone()["n"]

    if existing_admin_count > 0:
        return None

    admin_role_id = conn.execute(
        "SELECT id FROM roles WHERE name = 'administrator'"
    ).fetchone()["id"]

    temp_password = _generate_temp_password()
    pw_hash = hash_password(temp_password)

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    conn.execute(
        """
        INSERT INTO auth_users
            (username, display_name, password_hash, role_id, is_active,
             must_change_password, password_changed_at, created_at, created_by)
        VALUES (?, ?, ?, ?, 1, 1, ?, ?, NULL)
        """,
        (username, "Default Administrator", pw_hash, admin_role_id, now, now),
    )
    conn.commit()
    return temp_password


def run_migration(db_path: str | None = None, *, admin_username: str = "admin") -> dict:
    """
    Full migration entry point. Returns a small report dict:
        {
            "db_path": str,
            "bootstrap_admin_created": bool,
            "bootstrap_admin_username": str | None,
            "bootstrap_admin_temp_password": str | None,
        }
    The temp password key is populated only on first run (fresh DB
    with no administrator yet) — treat it as sensitive and do not log
    it to a file; print it to an interactive terminal only.
    """
    resolved_path = _resolve_db_path(db_path)
    conn = _connect(resolved_path)
    try:
        apply_schema(conn)
        seed_permissions(conn)
        seed_role_permissions(conn)
        temp_password = ensure_bootstrap_admin(conn, username=admin_username)
    finally:
        conn.close()

    return {
        "db_path": resolved_path,
        "bootstrap_admin_created": temp_password is not None,
        "bootstrap_admin_username": admin_username if temp_password is not None else None,
        "bootstrap_admin_temp_password": temp_password,
    }


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply the DWS authentication schema and seed roles/permissions."
    )
    parser.add_argument(
        "--db-path", default=None,
        help="Explicit SQLite file path. Defaults to dws_ros2.paths.DB_PATH.",
    )
    parser.add_argument(
        "--admin-username", default="admin",
        help="Username for the bootstrap administrator account (default: admin).",
    )
    args = parser.parse_args()

    report = run_migration(args.db_path, admin_username=args.admin_username)

    print(f"[auth_migration] Database: {report['db_path']}")
    print("[auth_migration] Schema applied, permissions and role mappings seeded.")
    if report["bootstrap_admin_created"]:
        print()
        print("=" * 64)
        print(" BOOTSTRAP ADMINISTRATOR ACCOUNT CREATED")
        print("=" * 64)
        print(f"  Username: {report['bootstrap_admin_username']}")
        print(f"  Temporary password: {report['bootstrap_admin_temp_password']}")
        print()
        print("  This password is shown ONCE and is not stored anywhere in")
        print("  plaintext. The account is flagged must_change_password, so")
        print("  the first login will require setting a new password.")
        print("=" * 64)
    else:
        print("[auth_migration] An administrator account already exists — "
              "no bootstrap account created.")


if __name__ == "__main__":
    _main()
