"""
health_api.py
==============
Reliability layer — REST API.

Runs as its own standalone FastAPI/uvicorn process (recommended port
8001, separate from `api_server.py`'s 8000 — see
RELIABILITY_IMPLEMENTATION_GUIDE.md), so that an outage of the *main*
API server doesn't also take down the endpoints an operator or Electron
shell would need precisely when something is wrong.

Run with:
    uvicorn health_api:app --host 0.0.0.0 --port 8001

Endpoints
---------
GET  /                health_api process self-check (this process only)
GET  /health           current top-level health summary (small, fast —
                        safe to poll every few seconds from a tray icon)
GET  /health/full       complete per-component health snapshot, including
                        process/heartbeat/resource detail
GET  /health/history    recent reliability events (state transitions,
                        recovery attempts, process deaths) for a timeline
                        view, optionally filtered by component
GET  /diagnostics       full diagnostic_report.py snapshot (uptime, CPU,
                        RAM, disk, hardware, DB, processes, last crashes,
                        version) — the same payload diagnostic_report.py
                        produces standalone, exposed over HTTP for
                        Electron / remote support use

These are intentionally read-only and side-effect-free (no
start/stop/restart action endpoints in Sprint 1 — see
RELIABILITY_IMPLEMENTATION_GUIDE.md "Out of scope for Sprint 1" for why
that's deliberate, not an oversight).
"""

from __future__ import annotations

import os
import sys
import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from heartbeat import HeartbeatReader, HeartbeatConfig  # noqa: E402
from health_manager import HealthManager, HealthState  # noqa: E402
from watchdog import WatchdogConfig  # noqa: E402
from diagnostic_report import generate_report  # noqa: E402

app = FastAPI(
    title="DWS Reliability API",
    version="1.0.0",
    description="Process/health/recovery telemetry for the DWS reliability layer",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_wd_cfg = WatchdogConfig()
_health_manager = HealthManager(db_path=_wd_cfg.db_path)
_heartbeat_reader = HeartbeatReader(HeartbeatConfig(db_path=_wd_cfg.db_path))

_PROCESS_STARTED_AT = time.time()


@app.get("/")
def root():
    return {
        "status": "running",
        "system": "DWS Reliability API",
        "version": "1.0.0",
        "uptime_seconds": round(time.time() - _PROCESS_STARTED_AT, 1),
    }


@app.get("/health")
def health_summary():
    """
    Small, fast top-level summary — safe to poll frequently (tray icon,
    Electron health badge). For full per-component detail use
    GET /health/full.
    """
    overall = _health_manager.overall_state()
    snapshot = _health_manager.snapshot()

    error_components = [n for n, c in snapshot.items() if c["state"] == HealthState.ERROR.value]
    warning_components = [n for n, c in snapshot.items() if c["state"] == HealthState.WARNING.value]

    return {
        "status": overall.value,
        "healthy": overall == HealthState.ONLINE,
        "error_components": error_components,
        "warning_components": warning_components,
        "checked_at": time.time(),
    }


@app.get("/health/full")
def health_full():
    """
    Complete per-component snapshot: state, reason, process liveness,
    heartbeat freshness, resource usage (CPU%/RSS), and PID — everything
    health_manager.HealthManager currently knows about every tracked
    component.
    """
    snapshot = _health_manager.snapshot()
    return {
        "overall_status": _health_manager.overall_state().value,
        "components": snapshot,
        "checked_at": time.time(),
    }


@app.get("/health/history")
def health_history(
    limit: int = Query(50, ge=1, le=500),
    component: str | None = Query(None, description="Filter to one component name"),
):
    """
    Recent reliability events: state transitions into/out of ERROR,
    recovery attempts and their outcomes, process-death detections, and
    heartbeat timeouts — sourced from the `reliability_events` table
    (heartbeat.record_event) plus the recovery action log
    (`reliability_recovery_log`, written by recovery_manager.py).
    """
    events = _heartbeat_reader.recent_events(limit=limit, component=component)

    recovery_log = []
    try:
        import sqlite3
        conn = sqlite3.connect(_wd_cfg.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            if component:
                rows = conn.execute(
                    "SELECT * FROM reliability_recovery_log WHERE component = ? ORDER BY id DESC LIMIT ?",
                    (component, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM reliability_recovery_log ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            recovery_log = [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        # Table may not exist yet if recovery_manager.py has never run
        # in this environment — an empty list is the honest answer, not
        # an error.
        recovery_log = []

    return {
        "events": events,
        "recovery_log": recovery_log,
        "returned_at": time.time(),
    }


@app.get("/diagnostics")
def diagnostics():
    """
    Full production diagnostic report: uptime, CPU, RAM, disk, camera,
    barcode, scale, database, running processes, last crashes, and
    software version. Delegates to diagnostic_report.generate_report()
    — the same function `diagnostic_report.py` uses when run standalone
    (e.g. from a support script or cron job writing a daily snapshot to
    disk), so this endpoint and that CLI tool can never drift apart.
    """
    try:
        return generate_report()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"diagnostic_report generation failed: {e}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("DWS_RELIABILITY_API_PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)
