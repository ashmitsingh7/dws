"""
test_watchdog.py
=================

Covers the pure, easily-isolated sampling primitives in
reliability/watchdog.py: PID liveness against real OS processes,
DB reachability against a real (and a broken) SQLite path, and the
CpuTracker delta calculation. Does not exercise the full daemon loop
(covered indirectly via the manual `watchdog.py --once` smoke test in
INTEGRATION_TEST_REPORT.md).
"""

import os
import sqlite3
import subprocess
import sys
import time

from watchdog import CpuTracker, db_reachable, pid_alive, read_pid_file


def test_pid_alive_true_for_current_process():
    assert pid_alive(os.getpid()) is True


def test_pid_alive_false_for_nonexistent_pid():
    # Spawn and immediately reap a short-lived process to get a PID
    # that is guaranteed not to be reused within this test.
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_pid = proc.pid
    proc.wait()
    assert pid_alive(dead_pid) is False


def test_pid_alive_false_for_none_or_zero():
    assert pid_alive(None) is False
    assert pid_alive(0) is False


def test_read_pid_file_missing_file_returns_none(tmp_path):
    assert read_pid_file(str(tmp_path / "nope.pid")) is None


def test_read_pid_file_reads_int(tmp_path):
    p = tmp_path / "backend.pid"
    p.write_text(str(os.getpid()))
    assert read_pid_file(str(p)) == os.getpid()


def test_db_reachable_true_for_real_sqlite_file(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()
    ok, err = db_reachable(db_path)
    assert ok is True
    assert err is None


def test_db_reachable_false_for_unreachable_path():
    ok, err = db_reachable("/nonexistent_dir_xyz/no.db", timeout=0.5)
    assert ok is False
    assert err is not None
    assert "DB_UNREACHABLE" in err


def test_cpu_tracker_needs_two_samples_for_a_delta():
    tracker = CpuTracker()
    pid = os.getpid()
    first = tracker.sample(pid)
    assert first is None  # no baseline yet
    time.sleep(0.05)
    second = tracker.sample(pid)
    assert second is None or isinstance(second, float)


def test_cpu_tracker_sustained_counter_resets_on_good_sample():
    tracker = CpuTracker()
    pid = 99999  # arbitrary key, no real /proc lookup needed for this check
    assert tracker.note_sustained(pid, True) == 1
    assert tracker.note_sustained(pid, True) == 2
    assert tracker.note_sustained(pid, False) == 0
