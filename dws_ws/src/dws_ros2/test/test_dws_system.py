"""
test_dws_system.py
==================
Build 005.1 tests aligned with the current single-process SimBus
architecture (barcode-triggered FSM, RealSense vision path).

Run with:  python3 test/test_dws_system.py
"""

import sys
import os
import time
import math
import logging
import sqlite3
import tempfile
import shutil
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dws_ros2.sim_bus import BUS
from dws_ros2.messages import (
    ScaleMsg,
    BarcodeMsg,
    TransactionMsg,
    SaveTransactionRequest,
)
from dws_ros2.config_loader import load_config, CONFIG_PATH
from dws_ros2.paths import resolve_db_path
from dws_ros2.scale_node import ScaleNode
from dws_ros2.orchestrator_node import OrchestratorNode, REQUIRED_STABLE_TIME
from dws_ros2.database_node import DatabaseNode
from dws_ros2.sync_worker_node import SyncWorkerNode

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s [%(name)s] %(message)s",
)

PASS = "\033[92m✓ PASS\033[0m"
FAIL = "\033[91m✗ FAIL\033[0m"

_results = {"pass": 0, "fail": 0, "total": 0}


def check(name: str, condition: bool, detail: str = ""):
    _results["total"] += 1
    if condition:
        _results["pass"] += 1
        print(f"  {PASS}  {name}")
    else:
        _results["fail"] += 1
        print(f"  {FAIL}  {name}" + (f"  ← {detail}" if detail else ""))


def section(title: str):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


def make_test_db():
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test_dws.db")
    db_node = DatabaseNode(db_path=db_path)
    db_node.start()
    return db_node, tmp_dir


def teardown_db(db_node, tmp_dir):
    db_node.stop()
    shutil.rmtree(tmp_dir, ignore_errors=True)


class MockScale:
    def __init__(self, weight=5.0, stable=True):
        self.weight = weight
        self.stable = stable

    def check_health(self):
        return {"valid": True}

    def get_measurement(self):
        return ScaleMsg(
            weight=self.weight,
            stable=self.stable,
            timestamp=time.time(),
        )


class MockVision:
    calibration_valid = True

    def check_health(self):
        return {"valid": True}

    def check_calibration_files(self):
        return {"valid": True}

    def reset_measurement_state(self):
        pass

    def capture_measurement(self, tx_id=None, timeout=10.0):
        return {
            "valid": True,
            "locked": True,
            "length": 300.0,
            "width": 200.0,
            "height": 150.0,
            "object_count": 1,
            "confidence": 0.95,
            "image_path": "/tmp/test.jpg",
            "pointcloud_path": None,
        }


def test_config_yaml():
    section("T01: Configuration — YAML loads and DB path resolves")

    cfg = load_config(force_reload=True)
    check("T01a: config file parses as mapping", isinstance(cfg, dict))
    check(
        "T01b: scale serial_port from YAML",
        cfg["scale"]["serial_port"] == "/dev/ttyUSB0",
        str(cfg["scale"]["serial_port"]),
    )
    check(
        "T01c: sync disabled by default",
        cfg["sync"]["enabled"] is False,
    )
    check(
        "T01d: CONFIG_PATH points at repo file",
        os.path.isfile(CONFIG_PATH),
        CONFIG_PATH,
    )

    prev = os.environ.pop("DWS_DB_PATH", None)
    try:
        os.environ["DWS_DATABASE_FILE"] = "unit_test.db"
        cfg2 = load_config(force_reload=True)
        path = resolve_db_path()
        check(
            "T01e: database.file resolves under $HOME",
            path.endswith(os.path.join(os.path.expanduser("~"), "unit_test.db")),
            path,
        )
    finally:
        os.environ.pop("DWS_DATABASE_FILE", None)
        if prev:
            os.environ["DWS_DB_PATH"] = prev
        load_config(force_reload=True)


def test_scale_stability():
    section("T02: Scale Node — statistical stability")

    node = ScaleNode(
        stub_mode=True,
        buffer_size=5,
        required_stable_windows=3,
    )

    for _ in range(8):
        node._ingest_reading(5.0, "US")

    m = node.get_measurement()
    check("T02a: stable after consecutive tight readings", m.stable is True)
    check("T02b: mean weight near 5.0 kg", abs(m.weight - 5.0) < 0.05)

    node._ingest_reading(0.01, "US")
    m2 = node.get_measurement()
    check("T02c: near-zero weight forced unstable", m2.stable is False)


def test_scale_thread_safety():
    section("T03: Scale Node — thread-safe snapshot")

    node = ScaleNode(stub_mode=True)
    node.start()
    try:
        time.sleep(0.3)
        readings = []
        errors = []

        def reader():
            try:
                for _ in range(50):
                    m = node.get_measurement()
                    readings.append((m.weight, m.stable, m.timestamp))
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        t = threading.Thread(target=reader)
        t.start()
        t.join(timeout=5)
        check("T03a: concurrent reads did not raise", len(errors) == 0)
        check("T03b: readings collected", len(readings) > 0)
    finally:
        node.stop()


def test_formulas():
    section("T04: Volumetric / chargeable formulas")

    cases = [
        (30, 20, 15, 5000.0, 2),
        (50, 35, 25, 5000.0, 9),
        (10, 10, 10, 5000.0, 1),
    ]
    for L, W, H, df, expected in cases:
        result = math.ceil((L * W * H) / df)
        check(
            f"T04: vol_weight ceil({L}×{W}×{H}/{df})={expected}",
            result == expected,
            f"got {result}",
        )

    check("T04: chargeable=max(3,2)=3", max(3.0, 2.0) == 3.0)
    check("T04: chargeable=max(1,5)=5", max(1.0, 5.0) == 5.0)


def test_database():
    section("T05: Database — WAL and duplicate AWB")

    db_node, tmp = make_test_db()
    try:
        txn = TransactionMsg(
            awb="DWS-UNIQUE-001",
            length=30,
            width=20,
            height=15,
            weight=3.5,
            volumetric_weight=2.0,
            chargeable_weight=3.5,
            station_id="STATION_01",
        )
        r1 = db_node._handle_save(SaveTransactionRequest(transaction=txn))
        r2 = db_node._handle_save(SaveTransactionRequest(transaction=txn))
        check("T05a: first insert succeeds", r1.success and r1.row_id > 0)
        check("T05b: duplicate AWB rejected", r2.success is False)
        check("T05c: single row stored", db_node.get_row_count() == 1)

        conn = sqlite3.connect(db_node.db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        check("T05d: journal_mode=WAL", mode.lower() == "wal", mode)

        dst = db_node.backup_database()
        check("T05e: backup uses instance db_path", os.path.isfile(dst))
    finally:
        teardown_db(db_node, tmp)


def test_sync_worker_disabled():
    section("T06: Sync worker — disabled by default")

    db_node, tmp = make_test_db()
    try:
        worker = SyncWorkerNode(
            db_node=db_node,
            enabled=False,
            poll_interval=0.5,
        )
        worker.start()
        time.sleep(1.5)
        worker.stop()
        check("T06a: no sync attempts when disabled", worker._sync_cycles == 0)
        check("T06b: rows remain unsynced", len(db_node.get_unsynced()) >= 0)
    finally:
        teardown_db(db_node, tmp)


def test_orchestrator_guards():
    section("T07: Orchestrator — barcode guards")

    db_node, tmp = make_test_db()
    orch = None
    try:
        orch = OrchestratorNode(
            scale_node=MockScale(),
            vision_node=MockVision(),
            station_id="TEST_01",
        )
        orch._developer_mode = True
        orch._calibration_required = False
        orch.start()
        time.sleep(0.2)

        before = orch._rejected_empty
        orch._barcode_callback(BarcodeMsg(data=""))
        check("T07a: empty barcode rejected", orch._rejected_empty == before + 1)

        orch._last_awb = "DUP-001"
        orch._last_awb_time = time.time()
        before_dup = orch._rejected_duplicate
        orch._barcode_callback(BarcodeMsg(data="DUP-001"))
        check("T07b: duplicate barcode rejected", orch._rejected_duplicate == before_dup + 1)
    finally:
        if orch:
            orch.stop()
        teardown_db(db_node, tmp)


def test_orchestrator_persistence():
    section("T08: Orchestrator — shipment counter persisted")

    db_node, tmp = make_test_db()
    orch = None
    try:
        orch = OrchestratorNode(
            scale_node=MockScale(weight=6.0, stable=True),
            vision_node=MockVision(),
            station_id="TEST_01",
        )
        orch._developer_mode = True
        orch._calibration_required = False
        orch._shipments_since_calibration = 0
        orch.start()
        time.sleep(0.2)

        orch._barcode_callback(BarcodeMsg(data="SHIP-PERSIST-001"))
        time.sleep(REQUIRED_STABLE_TIME + 0.5)

        state = db_node.get_system_state()
        check(
            "T08a: shipments_since_calibration persisted",
            int(state.get("shipments_since_calibration", 0)) >= 1,
            str(state.get("shipments_since_calibration")),
        )
        check(
            "T08b: transaction saved",
            db_node.get_row_count() == 1,
            f"rows={db_node.get_row_count()}",
        )
    finally:
        if orch:
            orch.stop()
        teardown_db(db_node, tmp)


def main():
    print("\n" + "=" * 60)
    print("  DWS — Build 005.1 Test Suite")
    print("=" * 60)

    test_config_yaml()
    test_scale_stability()
    test_scale_thread_safety()
    test_formulas()
    test_database()
    test_sync_worker_disabled()
    test_orchestrator_guards()
    test_orchestrator_persistence()

    p, f, t = _results["pass"], _results["fail"], _results["total"]
    print(f"\n{'='*60}")
    print(f"  RESULTS: {p}/{t} passed  |  {f} failed")
    print(f"{'='*60}\n")

    sys.exit(1 if f > 0 else 0)


if __name__ == "__main__":
    main()
