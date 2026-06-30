"""
conftest.py
============

Shared pytest fixtures for the reliability-layer test suite.

Mirrors test/auth/conftest.py's convention: every test gets a
brand-new temp SQLite file (not in-memory, since some tests inspect
the DB with a second connection) so tests never interfere with each
other's heartbeat/health-state rows.
"""

import os
import sys
import tempfile

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.dirname(os.path.dirname(_TESTS_DIR))
_RELIABILITY_DIR = os.path.join(_SRC_DIR, "reliability")

if _RELIABILITY_DIR not in sys.path:
    sys.path.insert(0, _RELIABILITY_DIR)


@pytest.fixture()
def db_path():
    """A fresh, empty SQLite file path for one test."""
    fd, path = tempfile.mkstemp(prefix="dws_reliability_test_", suffix=".db")
    os.close(fd)
    os.remove(path)  # let schema-creation code create it fresh
    yield path
    for ext in ("", "-wal", "-shm"):
        try:
            os.remove(path + ext)
        except OSError:
            pass


@pytest.fixture()
def run_dir(tmp_path):
    """A fresh temp directory standing in for $DWS_RUN_DIR (PID files)."""
    d = tmp_path / "run"
    d.mkdir()
    return str(d)
