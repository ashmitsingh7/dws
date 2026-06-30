"""
conftest.py
===========

Shared pytest fixtures for the authentication test suite.

Each test gets a brand-new, freshly-migrated SQLite database (a
temp file, not in-memory, because some tests open a second
connection to simulate a second request) so tests never interfere
with each other's lockout counters, session rows, or audit logs.
"""

import os
import sys
import sqlite3
import tempfile

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.dirname(os.path.dirname(_TESTS_DIR))
_BUILD_DIR = _SRC_DIR
_AUTH_DIR = os.path.join(_BUILD_DIR, "authentication")
_DB_DIR = os.path.join(_BUILD_DIR, "database")

for _p in (_AUTH_DIR, _DB_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from auth_migration import run_migration  # noqa: E402


@pytest.fixture()
def db_path(tmp_path):
    path = str(tmp_path / "test_dws.db")
    return path


@pytest.fixture()
def migrated_db(db_path):
    """Runs the migration, returns (db_path, bootstrap_admin_password)."""
    report = run_migration(db_path)
    assert report["bootstrap_admin_created"] is True
    return db_path, report["bootstrap_admin_temp_password"]


@pytest.fixture()
def conn(migrated_db):
    db_path, _ = migrated_db
    c = sqlite3.connect(db_path, check_same_thread=False, timeout=10.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    yield c
    c.close()


@pytest.fixture()
def admin_password(migrated_db):
    return migrated_db[1]
