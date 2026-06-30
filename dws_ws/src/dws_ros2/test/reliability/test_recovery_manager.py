"""
test_recovery_manager.py
=========================

Covers reliability/recovery_manager.py with dry_run=True throughout
(never invokes a real run.sh/systemctl restart from a test process):
exponential backoff gating, max_attempts -> RECOVERY_EXHAUSTED, OS
restart de-duplication, and in-process soft-recovery request writes
for backend-internal subsystems (vision/scale/barcode/orchestrator).
"""

import sqlite3
import time

from health_manager import HealthManager, HealthState
from recovery_manager import RecoveryConfig, RecoveryManager


def _make_manager(db_path, **config_overrides):
    hm = HealthManager(db_path=db_path)
    defaults = dict(
        dry_run=True,
        base_delay_seconds=0.05,
        max_delay_seconds=0.2,
        min_seconds_between_os_restarts=0.05,
    )
    defaults.update(config_overrides)
    cfg = RecoveryConfig(**defaults)
    rm = RecoveryManager(hm, config=cfg, db_path=db_path)
    return hm, rm


def test_attempt_recovery_dispatches_os_restart_for_api_server(db_path):
    hm, rm = _make_manager(db_path)
    hm.report_sample("api_server", process_alive=False)  # -> ERROR, fires _on_transition
    st = rm._state["api_server"]
    assert st.attempts == 1


def test_backoff_blocks_immediate_second_attempt(db_path):
    hm, rm = _make_manager(db_path, base_delay_seconds=5.0)
    ok1 = rm.attempt_recovery("api_server")
    assert ok1 is True
    ok2 = rm.attempt_recovery("api_server")  # called again immediately
    assert ok2 is False  # still inside the backoff window


def test_max_attempts_reached_logs_recovery_exhausted(db_path):
    hm, rm = _make_manager(db_path, max_attempts=2, base_delay_seconds=0.01, max_delay_seconds=0.02)
    rm.attempt_recovery("hmi_server")
    time.sleep(0.05)
    rm.attempt_recovery("hmi_server")
    time.sleep(0.05)
    ok3 = rm.attempt_recovery("hmi_server")  # exceeds max_attempts=2
    assert ok3 is False

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT * FROM reliability_events WHERE component=? AND event_type=?",
        ("hmi_server", "RECOVERY_EXHAUSTED"),
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_in_process_subsystem_writes_recovery_request(db_path):
    hm, rm = _make_manager(db_path)
    hm.report_sample("vision_node", process_alive=False)  # -> ERROR -> recovery dispatch

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT * FROM reliability_recovery_requests WHERE component=?",
        ("vision_node",),
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_dry_run_never_marks_consecutive_online_until_recovered(db_path):
    hm, rm = _make_manager(db_path)
    hm.report_sample("scale_node", process_alive=False)
    assert "scale_node" in rm._state
    assert rm._state["scale_node"].consecutive_online_since is None


def test_status_reports_per_component_backoff_state(db_path):
    hm, rm = _make_manager(db_path)
    hm.report_sample("barcode_node", process_alive=False)
    status = rm.status()
    assert "barcode_node" in status
    assert status["barcode_node"]["attempts"] >= 1
