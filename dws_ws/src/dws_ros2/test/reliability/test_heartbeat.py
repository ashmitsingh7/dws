"""
test_heartbeat.py
==================

Covers reliability/heartbeat.py: write/read round-trip, the
min-write-gap throttle, staleness detection via timed_out, and the
event log (STARTED/STOPPED).
"""

import time

from heartbeat import HeartbeatConfig, HeartbeatEmitter, HeartbeatReader


def test_beat_then_read_round_trip(db_path):
    cfg = HeartbeatConfig(db_path=db_path)
    hb = HeartbeatEmitter("scale_node", config=cfg)
    hb.beat(status="ok", detail="reading stable")

    reader = HeartbeatReader(config=cfg)
    rec = reader.get("scale_node")
    assert rec is not None
    assert rec.status == "ok"
    assert rec.detail == "reading stable"
    assert rec.seq == 1
    assert rec.age_seconds < 2.0
    assert rec.timed_out is False


def test_absent_component_is_none_not_an_error(db_path):
    cfg = HeartbeatConfig(db_path=db_path)
    reader = HeartbeatReader(config=cfg)
    assert reader.get("vision_node") is None
    assert reader.snapshot() == {}


def test_min_write_gap_throttles_rapid_beats(db_path):
    cfg = HeartbeatConfig(db_path=db_path)
    hb = HeartbeatEmitter("barcode_node", config=cfg)
    hb.beat(status="ok")
    hb.beat(status="ok")  # called immediately after; should be throttled
    hb.beat(status="ok")

    reader = HeartbeatReader(config=cfg)
    rec = reader.get("barcode_node")
    # Only the first beat within the min-write-gap window should have
    # actually persisted a DB write (seq stays at 1).
    assert rec.seq == 1


def test_stale_heartbeat_flagged_timed_out(db_path):
    cfg = HeartbeatConfig(db_path=db_path, timeouts={"scale_node": 0.05})
    hb = HeartbeatEmitter("scale_node", config=cfg)
    hb.beat(status="ok")
    time.sleep(0.2)

    reader = HeartbeatReader(config=cfg)
    rec = reader.get("scale_node")
    assert rec.timed_out is True


def test_stop_records_stopped_event(db_path):
    cfg = HeartbeatConfig(db_path=db_path)
    hb = HeartbeatEmitter("hmi_server", config=cfg)
    hb.beat(status="ok")
    hb.stop()

    reader = HeartbeatReader(config=cfg)
    events = reader.recent_events(component="hmi_server")
    types = [e["event_type"] for e in events]
    assert "STOPPED" in types
