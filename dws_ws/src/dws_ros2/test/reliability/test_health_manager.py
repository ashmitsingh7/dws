"""
test_health_manager.py
=======================

Covers the state machine in reliability/health_manager.py:
hysteresis on the way down (good->bad), hysteresis on the way up
(bad->good), the hard-signal bypass for a dead process, the
STARTING grace window, and mark_deliberately_stopped.
"""

import time

from health_manager import HealthManager, HealthState


def test_fresh_component_starts_in_starting_state(db_path):
    hm = HealthManager(db_path=db_path)
    ch = hm.report_sample("scale_node", process_alive=True, check_ok=True)
    assert ch.state == HealthState.STARTING


def test_dead_process_bypasses_hysteresis_straight_to_error(db_path):
    hm = HealthManager(db_path=db_path)
    # Even on the very first sample, a hard process-death signal must
    # not wait for 3 consecutive bad samples.
    ch = hm.report_sample("api_server", process_alive=False)
    assert ch.state == HealthState.ERROR


def test_warning_requires_three_consecutive_bad_before_error(db_path):
    hm = HealthManager(db_path=db_path)
    # Force past the STARTING grace window by backdating last_change_at.
    hm.report_sample("vision_node", process_alive=True, check_ok=True)
    hm._components["vision_node"].last_change_at = time.time() - 9999
    hm._components["vision_node"].state = HealthState.ONLINE

    # 1st and 2nd bad sample -> WARNING, not yet ERROR.
    ch = hm.report_sample("vision_node", process_alive=True, check_ok=False, check_detail="frame stale")
    assert ch.state == HealthState.WARNING
    ch = hm.report_sample("vision_node", process_alive=True, check_ok=False, check_detail="frame stale")
    assert ch.state == HealthState.WARNING

    # 3rd consecutive bad sample -> escalates to ERROR.
    ch = hm.report_sample("vision_node", process_alive=True, check_ok=False, check_detail="frame stale")
    assert ch.state == HealthState.ERROR


def test_recovery_requires_two_consecutive_good_samples(db_path):
    hm = HealthManager(db_path=db_path)
    comp = hm._components.setdefault("scale_node", hm._components.get("scale_node"))
    hm.report_sample("scale_node", process_alive=True, check_ok=False)
    hm._components["scale_node"].state = HealthState.ERROR
    hm._components["scale_node"].consecutive_bad = 3
    hm._components["scale_node"].consecutive_good = 0

    ch = hm.report_sample("scale_node", process_alive=True, check_ok=True)
    assert ch.state == HealthState.ERROR  # only 1 good sample so far

    ch = hm.report_sample("scale_node", process_alive=True, check_ok=True)
    assert ch.state == HealthState.ONLINE  # 2nd consecutive good -> recovered


def test_mark_deliberately_stopped_reports_offline(db_path):
    hm = HealthManager(db_path=db_path)
    hm.report_sample("barcode_node", process_alive=True, check_ok=True)
    hm.mark_deliberately_stopped("barcode_node", True)
    ch = hm.report_sample("barcode_node", process_alive=False)
    assert ch.state == HealthState.OFFLINE
    hm.mark_deliberately_stopped("barcode_node", False)
    ch = hm.report_sample("barcode_node", process_alive=True, check_ok=True)
    assert ch.state != HealthState.OFFLINE


def test_transition_listener_fires_on_state_change(db_path):
    hm = HealthManager(db_path=db_path)
    seen = []
    hm.on_transition(lambda name, old, new: seen.append((name, old, new)))
    hm.report_sample("hmi_server", process_alive=False)
    assert len(seen) == 1
    assert seen[0][0] == "hmi_server"
    assert seen[0][2] == HealthState.ERROR


def test_snapshot_and_overall_state_reflect_worst_component(db_path):
    hm = HealthManager(db_path=db_path)
    hm.report_sample("vision_node", process_alive=True, check_ok=True)
    hm.report_sample("scale_node", process_alive=False)
    snap = hm.snapshot()
    assert "vision_node" in snap and "scale_node" in snap
    assert hm.overall_state() == HealthState.ERROR


def test_state_persists_across_new_manager_instance(db_path):
    hm1 = HealthManager(db_path=db_path)
    hm1.report_sample("database_node", process_alive=False)
    assert hm1.get("database_node").state == HealthState.ERROR

    # A second HealthManager pointed at the same DB file (simulating
    # health_api.py running in a separate process) must see the same
    # persisted state.
    hm2 = HealthManager(db_path=db_path)
    ch = hm2.get("database_node")
    assert ch is not None
    assert ch.state == HealthState.ERROR
