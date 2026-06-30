#!/usr/bin/env python3
"""
dws_simulate.py
===============
Full end-to-end DWS+ simulation runner.

Starts ALL nodes simultaneously with realistic stub traffic and prints
a live console dashboard showing every transaction as it flows through
the pipeline. Runs for DURATION seconds (default 120) then prints a
full session report and queries the database.

Usage:
    python3 dws_simulate.py              # 120s run
    python3 dws_simulate.py --duration 30
    python3 dws_simulate.py --verbose    # shows all node DEBUG logs
    python3 dws_simulate.py --duration 30 --verbose

What you will see:
  [SCALE]   weight readings as they stabilize
  [VISION]  dimension readings entering the queue
  [BARCODE] scan triggers
  [ORCH]    transaction builds and service call
  [DB]      save confirmation + row ID
  [HMI]     final transaction broadcast
  [SYNC]    cloud push attempts every 10s

Architecture verified:
  Camera fires → dims enter FIFO queue
  Scale fires  → weight enters FIFO queue
  Barcode fires → FIFO pops oldest of each → transaction → DB → HMI → Cloud
"""

import sys
import os
import time
import argparse
import logging
import threading
import signal

# Add src to path so imports work from any directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dws_ros2.sim_bus import BUS
from dws_ros2.scale_node import ScaleNode
from dws_ros2.vision_node import VisionNode
from dws_ros2.barcode_node import BarcodeNode
from dws_ros2.orchestrator_node import OrchestratorNode
from dws_ros2.database_node import DatabaseNode
from dws_ros2.hmi_bridge_node import HmiBridgeNode
from dws_ros2.sync_worker_node import SyncWorkerNode
from dws_ros2.messages import TransactionMsg

# ─────────────────────────────────────────────────────────────
# Argument parsing
# ─────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="DWS+ ROS2 Full Simulation")
parser.add_argument("--duration", type=int, default=120,
                    help="Simulation duration in seconds (default 120)")
parser.add_argument("--verbose", action="store_true",
                    help="Show DEBUG logs from all nodes")
parser.add_argument("--db", type=str, default=os.path.expanduser("~/dws_sim.db"),
                    help="SQLite DB path (default ~/dws_sim.db)")
parser.add_argument("--station", type=str, default="STATION_01",
                    help="Station ID (default STATION_01)")
args = parser.parse_args()

# ─────────────────────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────────────────────

LOG_LEVEL = logging.DEBUG if args.verbose else logging.INFO

# Custom formatter with colour
RESET = "\033[0m"
COLOURS = {
    "scale_node":        "\033[36m",   # cyan
    "vision_node":       "\033[34m",   # blue
    "barcode_node":      "\033[35m",   # magenta
    "orchestrator_node": "\033[33m",   # yellow
    "database_node":     "\033[32m",   # green
    "hmi_bridge_node":   "\033[96m",   # bright cyan
    "sync_worker_node":  "\033[90m",   # dark grey
    "bus":               "\033[37m",   # white
    "SIM":               "\033[97m",   # bright white
}


class ColouredFormatter(logging.Formatter):
    def format(self, record):
        col = COLOURS.get(record.name, "\033[37m")
        tag = record.name.upper().replace("_NODE", "").replace("_", "")[:6].ljust(6)
        lvl = "WRN" if record.levelno == logging.WARNING else \
              "ERR" if record.levelno == logging.ERROR else \
              "DBG" if record.levelno == logging.DEBUG else "INF"
        ts = time.strftime("%H:%M:%S")
        msg = record.getMessage()
        return f"{col}[{ts}][{tag}][{lvl}] {msg}{RESET}"


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(ColouredFormatter())
logging.root.setLevel(LOG_LEVEL)
logging.root.addHandler(handler)

sim_log = logging.getLogger("SIM")

# ─────────────────────────────────────────────────────────────
# Remove old DB if it exists (fresh run)
# ─────────────────────────────────────────────────────────────

if os.path.exists(args.db):
    os.remove(args.db)
    sim_log.info(f"Removed old DB: {args.db}")

# ─────────────────────────────────────────────────────────────
# Instantiate nodes
# ─────────────────────────────────────────────────────────────

db_node    = DatabaseNode(db_path=args.db)
orch_node  = OrchestratorNode(dim_factor=5000.0, station_id=args.station)
hmi_node   = HmiBridgeNode(port=8765)
scale_node = ScaleNode(stub_mode=True, station_id=args.station)
vision_node = VisionNode(stub_mode=True, station_id=args.station)
barcode_node = BarcodeNode(stub_mode=True, station_id=args.station)
sync_node  = SyncWorkerNode(db_node=db_node, poll_interval=10.0)

# ─────────────────────────────────────────────────────────────
# HMI live dashboard hook
# ─────────────────────────────────────────────────────────────

_txn_count = 0
_txn_lock = threading.Lock()

def _hmi_dashboard(payload: dict):
    global _txn_count
    with _txn_lock:
        _txn_count += 1
        n = _txn_count

    print(f"\n{'═'*64}")
    print(f"  📦 TRANSACTION #{n:03d}  |  AWB: {payload['awb']}")
    print(f"{'─'*64}")
    print(f"  Dimensions  : {payload['l']} × {payload['w']} × {payload['h']} cm")
    print(f"  Actual wt   : {payload['wt']:.3f} kg")
    print(f"  Volumetric  : {payload['vol_wt']:.1f} kg  "
          f"[L×W×H / 5000 = {payload['l']*payload['w']*payload['h']:.0f} / 5000]")
    print(f"  Chargeable  : {payload['charge_wt']:.1f} kg  "
          f"({'actual' if payload['wt'] >= payload['vol_wt'] else 'volumetric'} wins)")
    print(f"  Station     : {payload['station']}")
    print(f"  Saved at    : {payload['ts_str']}")
    print(f"  Sync status : {'✓ synced' if payload['sync'] else '○ pending sync'}")
    print(f"{'═'*64}\n")

hmi_node.register_ui_hook(_hmi_dashboard)

# ─────────────────────────────────────────────────────────────
# Start all nodes  (order matters: DB first, then orch, then sensors)
# ─────────────────────────────────────────────────────────────

sim_log.info("=" * 64)
sim_log.info(f" DWS+ ROS2 SIMULATION STARTING")
sim_log.info(f" Duration  : {args.duration}s")
sim_log.info(f" DB path   : {args.db}")
sim_log.info(f" Station   : {args.station}")
sim_log.info(f" Verbose   : {args.verbose}")
sim_log.info("=" * 64)

db_node.start()
orch_node.start()
hmi_node.start()
sync_node.start()

time.sleep(0.3)   # let services register before sensors start

scale_node.start()
vision_node.start()
barcode_node.start()

# ─────────────────────────────────────────────────────────────
# Graceful shutdown on Ctrl+C
# ─────────────────────────────────────────────────────────────

_shutdown = threading.Event()

def _sigint_handler(sig, frame):
    sim_log.info("Ctrl+C — shutting down...")
    _shutdown.set()

signal.signal(signal.SIGINT, _sigint_handler)

# ─────────────────────────────────────────────────────────────
# Progress ticker
# ─────────────────────────────────────────────────────────────

start_time = time.time()

def _progress():
    while not _shutdown.is_set():
        elapsed = int(time.time() - start_time)
        remaining = max(0, args.duration - elapsed)
        stats = orch_node.get_stats()
        sync_stats = sync_node.get_stats()
        sys.stdout.write(
            f"\r  ⏱  {elapsed:3d}s / {args.duration}s  |  "
            f"📦 TX: {stats['total_transactions']}  |  "
            f"☁  Synced: {sync_stats['total_synced']}  |  "
            f"Dim Q: {stats['dim_queue_len']}  "
            f"Wt Q: {stats['weight_queue_len']}  |  "
            f"Rejected — conf: {stats['rejected_low_confidence']} "
            f"dup: {stats['rejected_duplicate']} "
            f"empty: {stats['rejected_empty_barcode']}   "
        )
        sys.stdout.flush()
        time.sleep(1.0)

progress_thread = threading.Thread(target=_progress, daemon=True)
progress_thread.start()

# ─────────────────────────────────────────────────────────────
# Main wait loop
# ─────────────────────────────────────────────────────────────

try:
    _shutdown.wait(timeout=args.duration)
except Exception:
    pass

_shutdown.set()
time.sleep(0.5)
print()   # newline after progress ticker

# ─────────────────────────────────────────────────────────────
# Shutdown all nodes
# ─────────────────────────────────────────────────────────────

sim_log.info("Stopping nodes...")
barcode_node.stop()
vision_node.stop()
scale_node.stop()
sync_node.stop()
orch_node.stop()
hmi_node.stop()
BUS.shutdown()
time.sleep(0.5)

# ─────────────────────────────────────────────────────────────
# Final session report
# ─────────────────────────────────────────────────────────────

orch_stats = orch_node.get_stats()
sync_stats = sync_node.get_stats()
rows = db_node.get_all()
db_node.stop()

print()
print("╔" + "═"*62 + "╗")
print("║" + "  SESSION REPORT".center(62) + "║")
print("╠" + "═"*62 + "╣")
print(f"║  Duration          : {int(time.time()-start_time)}s".ljust(63) + "║")
print(f"║  Total transactions: {orch_stats['total_transactions']}".ljust(63) + "║")
print(f"║  Rows in DB        : {len(rows)}".ljust(63) + "║")
print(f"║  Cloud synced      : {sync_stats['total_synced']}".ljust(63) + "║")
print(f"║  Sync failures     : {sync_stats['total_failed']}".ljust(63) + "║")
print(f"║  Rejected (low-conf): {orch_stats['rejected_low_confidence']}".ljust(63) + "║")
print(f"║  Rejected (dup scan): {orch_stats['rejected_duplicate']}".ljust(63) + "║")
print(f"║  Rejected (empty)  : {orch_stats['rejected_empty_barcode']}".ljust(63) + "║")
print(f"║  Rejected (timeout): {orch_stats['rejected_sensor_timeout']}".ljust(63) + "║")
print("╠" + "═"*62 + "╣")
print("║  LAST 5 TRANSACTIONS FROM DB:".ljust(63) + "║")

if rows:
    print("║  " + f"{'ID':>3}  {'AWB':<22} {'L':>5} {'W':>5} {'H':>5} {'WT':>6} {'CHG':>6} {'SY':>3}".ljust(60) + "  ║")
    print("║  " + "─"*60 + "  ║")
    for r in rows[-5:]:
        rid, awb, l, w, h, wt, vwt, cwt, station, ts, sync = r
        line = f"{rid:>3}  {awb:<22} {l:>5.1f} {w:>5.1f} {h:>5.1f} {wt:>6.3f} {cwt:>6.1f} {'✓' if sync else '○':>3}"
        print("║  " + line.ljust(60) + "  ║")
else:
    print("║  (no rows — simulation may have run too short)".ljust(63) + "║")

print("╚" + "═"*62 + "╝")
print(f"\n  DB saved to: {args.db}\n")



# ─────────────────────────────────────────────────────────────
# ROS2 executable entry point
# ─────────────────────────────────────────────────────────────

def main(args=None):
    """
    Required so ROS2 can execute:

        ros2 run dws_ros2 dws_simulate
    """
    return


if __name__ == "__main__":
    main()
