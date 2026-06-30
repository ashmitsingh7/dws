"""
api_server.py
=============
FastAPI REST interface for the DWS system.

Run with:
    uvicorn api_server:app --host 0.0.0.0 --port 8000

Endpoints
---------
GET  /                    health ping
GET  /health              connection + count check
GET  /stats               totals
GET  /latest              most recent transaction (full fields)
GET  /history             paginated transaction list
GET  /errors              recent error log (severity/component filters)
GET  /session             session_stats table
GET  /dashboard           composite summary
GET  /metrics             averages
GET  /system              station info + developer_mode/calibration status
GET  /settings            current editable settings (all 6 fields)
POST /settings            update settings (all 6 fields)
GET  /error_breakdown     error type breakdown
GET  /success_rate        pass/fail ratio
GET  /weight_distribution histogram
GET  /billing             chargeable vs actual
GET  /scanner             online/offline probe
GET  /export/csv          export transactions as CSV (date range or current shift)
GET  /export/pdf          export transactions as a multi-page PDF summary (date range)
GET  /export/transaction/{id}/pdf  single-transaction PDF report
POST /backup              trigger DB backup, returns filename
GET  /calibration/latest  most recent calibration result
GET  /captures/{name}     serve a captured JPEG
GET  /pointclouds/{name}  serve a .ply point-cloud file (download)
GET  /system/version      app version + git commit + build timestamp
GET  /system/metrics      real cpu_percent/memory/disk (no psutil; /proc-based)
GET  /logs                audit log feed (backed by auth_login_history)
POST /pipeline/start      set system_state.pipeline_running = 1 (reporting only)
POST /pipeline/stop       set system_state.pipeline_running = 0 (reporting only)
POST /scale/tare          always 403 — software tare is permanently disabled
"""

import os
import io
import csv
import time
import shutil

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import sqlite3

import pdf_export

# ---------------------------------------------------------------------------
# Authentication — import auth router and migration.
# Both imports are wrapped in try/except so api_server.py continues to
# start (with auth routes absent) even if the authentication package is
# somehow not present; in normal deployment this will always succeed.
# ---------------------------------------------------------------------------

try:
    from authentication.auth_api import auth_router
    from database.auth_migration import run_migration as _run_auth_migration
    _AUTH_AVAILABLE = True
except ImportError as _auth_import_err:
    auth_router = None
    _run_auth_migration = None
    _AUTH_AVAILABLE = False
    import logging as _logging
    _logging.getLogger("dws.api").warning(
        "Authentication package not importable (%s); /auth and /admin routes will be absent.",
        _auth_import_err,
    )


# ---------------------------------------------------------------------------
# Reliability — diagnostic_report supplies the /proc-based system-resource
# collectors (CPU/RAM/disk, version + git info) used by GET /system/version
# and GET /system/metrics below. Same degrade-gracefully-on-ImportError
# pattern as the authentication import above: api_server.py must keep
# starting even if reliability/ is absent for some reason.
# ---------------------------------------------------------------------------

try:
    import sys as _sys
    _RELIABILITY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reliability")
    if _RELIABILITY_DIR not in _sys.path:
        _sys.path.insert(0, _RELIABILITY_DIR)
    import diagnostic_report as _diagnostic_report
    _RELIABILITY_AVAILABLE = True
except ImportError as _reliability_import_err:
    _diagnostic_report = None
    _RELIABILITY_AVAILABLE = False
    import logging as _logging
    _logging.getLogger("dws.api").warning(
        "Reliability package not importable (%s); /system/version and "
        "/system/metrics will return degraded responses.",
        _reliability_import_err,
    )


# ---------------------------------------------------------------------------
# Paths — resolve from environment, then from the shared dws_ros2.paths
# resolver, so this standalone script stays consistent with the rest of the
# pipeline regardless of which user account or layout (source checkout vs.
# colcon install) is running it.
# ---------------------------------------------------------------------------

try:
    from dws_ros2.paths import (
        DB_PATH,
        CAPTURE_DIR,
        POINTCLOUD_DIR,
        BACKUP_DIR,
    )
except ImportError:
    _HOME = os.path.expanduser("~")
    DB_PATH = os.path.join(_HOME, "dws_data.db")
    CAPTURE_DIR = os.path.join(_HOME, "dws_ws", "src", "dws_ros2", "captures")
    POINTCLOUD_DIR = os.path.join(_HOME, "dws_ws", "captures", "pointclouds")
    BACKUP_DIR = os.path.join(_HOME, "dws_ws", "src", "dws_ros2", "backups")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="DWS API",
    version="1.0.1",
    description="Dimensioning, Weighing & Scanning system REST interface",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount auth router (adds /auth/* and /admin/* routes)
if _AUTH_AVAILABLE and auth_router is not None:
    app.include_router(auth_router)


@app.on_event("startup")
def _startup_auth_migration():
    """Run auth DB migration once at process startup (idempotent, non-destructive)."""
    if _AUTH_AVAILABLE and _run_auth_migration is not None:
        try:
            report = _run_auth_migration(DB_PATH)
            import logging as _logging
            log = _logging.getLogger("dws.api")
            log.info("[auth_migration] Schema applied. DB: %s", report["db_path"])
            if report.get("bootstrap_admin_created"):
                log.warning(
                    "[auth_migration] Bootstrap admin '%s' created. "
                    "Temporary password printed to stdout — change it immediately.",
                    report["bootstrap_admin_username"],
                )
                print(
                    "\n" + "=" * 64 + "\n"
                    " BOOTSTRAP ADMINISTRATOR ACCOUNT CREATED\n"
                    "=" * 64 + "\n"
                    f"  Username: {report['bootstrap_admin_username']}\n"
                    f"  Temporary password: {report['bootstrap_admin_temp_password']}\n"
                    "  This password is shown ONCE. The account is flagged\n"
                    "  must_change_password. Change it at first login.\n"
                    + "=" * 64 + "\n"
                )
        except Exception as exc:
            import logging as _logging
            _logging.getLogger("dws.api").error("[auth_migration] Migration failed: %s", exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_transaction(row: sqlite3.Row) -> dict:
    """Convert a transactions table row into a clean API dict."""
    keys = row.keys()
    return {
        "id":              row["id"],
        "awb":             row["awb"],
        "tx_id":           row["tx_id"] if "tx_id" in keys else None,
        "length":          row["length"],
        "width":           row["width"],
        "height":          row["height"],
        "weight":          row["weight"],
        "object_count":    row["object_count"] if "object_count" in keys else 1,
        "vol_weight":      row["vol_weight"],
        "charge_weight":   row["charge_weight"],
        "dim_factor":      row["dim_factor"],
        "station_id":      row["station_id"],
        "image_url":       row["image_url"] if "image_url" in keys else None,
        "pointcloud_url":  row["pointcloud_url"] if "pointcloud_url" in keys else None,
        "timestamp":       row["timestamp"],
        "created_at":      row["created_at"],
        "sync_status":     row["sync_status"],
        "latency_ms":      row["latency_ms"] if "latency_ms" in keys else None,
        "operator":        row["operator"] if "operator" in keys else None,
        "confidence":      row["confidence"] if "confidence" in keys else 1.0,
    }


# ---------------------------------------------------------------------------
# Core routes
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {"status": "running", "system": "DWS", "version": "1.0.1", "build": "005.1"}


@app.get("/health")
def health():
    """
    Honest composite health check.

    Reads the `health_status` row written every
    `health.check_interval_seconds` by HealthMonitor (running inside
    the main DWS process — see dws_ros2/health_monitor.py), which
    performs *real* probes:
      - camera: live frame read via VisionNodeRealSense.check_health()
      - scale:  reading-age probe via ScaleNode.check_health()
      - disk:   shutil.disk_usage() against the capture volume
      - calibration: required .npy files present, loadable, and
                      structurally valid
    plus a direct SQLite query for the database check below (this
    process's own connection, independent of HealthMonitor's), and
    a defensive read of system_state.developer_mode so a single
    /health call gives a full ops view without also hitting
    /system. developer_mode does not affect overall_ok/status here
    — it's informational only, same as on /system.

    If the health_status row is missing entirely (older DB, or the
    main DWS process has never run since this build) or hasn't been
    updated recently, that is reported explicitly rather than
    silently reporting "healthy".
    """
    db_ok = False
    db_error = None
    tx_count = None
    row = None
    developer_mode = None

    try:
        conn = get_conn()
        tx_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        db_ok = True
        try:
            cur = conn.execute("SELECT * FROM health_status WHERE id=1")
            r = cur.fetchone()
            row = dict(r) if r else None
        except sqlite3.OperationalError:
            # health_status table doesn't exist — pre-Build-002 DB file
            row = None
        try:
            # developer_mode lives in system_state, not health_status —
            # read it separately so a missing/old health_status table
            # doesn't also hide this. Same defensive pattern as /system.
            dm_row = conn.execute(
                "SELECT developer_mode FROM system_state WHERE id=1"
            ).fetchone()
            if dm_row and dm_row["developer_mode"] is not None:
                developer_mode = bool(dm_row["developer_mode"])
        except sqlite3.OperationalError:
            pass
        conn.close()
    except Exception as e:
        db_error = str(e)

    if row is None or not row.get("checked_at"):
        return {
            "status": "unknown",
            "database": {"ok": db_ok, "transactions": tx_count, "error": db_error},
            "camera": {"ok": None, "reason": "NO_HEALTH_SNAPSHOT_YET"},
            "scale": {"ok": None, "reason": "NO_HEALTH_SNAPSHOT_YET"},
            "disk": {"ok": None, "reason": "NO_HEALTH_SNAPSHOT_YET"},
            "calibration": {"ok": None, "reason": "NO_HEALTH_SNAPSHOT_YET"},
            "developer_mode": developer_mode,
            "message": (
                "No health snapshot found. The main DWS backend "
                "(run_dws.py / systemd service) must be running for "
                "camera/scale/disk/calibration checks to populate — "
                "this API process alone can only verify the database."
            ),
        }

    checked_at = row.get("checked_at") or 0
    age_seconds = max(0.0, time.time() - checked_at) if checked_at else None
    stale = age_seconds is None or age_seconds > 60  # >12 missed cycles at default 5s

    overall_ok = bool(row.get("overall_ok")) and db_ok and not stale

    return {
        "status": "healthy" if overall_ok else "degraded",
        "stale": stale,
        "snapshot_age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
        "checked_at": row.get("checked_at_iso"),
        "developer_mode": developer_mode,
        "database": {"ok": db_ok, "transactions": tx_count, "error": db_error},
        "camera": {"ok": bool(row.get("camera_ok")), "reason": row.get("camera_reason")},
        "scale": {"ok": bool(row.get("scale_ok")), "reason": row.get("scale_reason")},
        "disk": {
            "ok": bool(row.get("disk_ok")),
            "free_gb": row.get("disk_free_gb"),
            "reason": row.get("disk_reason"),
        },
        "calibration": {
            "ok": bool(row.get("calibration_ok")),
            "reason": row.get("calibration_reason"),
        },
    }


@app.get("/stats")
def stats():
    conn = get_conn()
    total_tx  = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    total_err = conn.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
    last_row  = conn.execute(
        "SELECT created_at FROM transactions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    mean_latency = conn.execute(
        "SELECT AVG(latency_ms) FROM transactions WHERE latency_ms > 0"
    ).fetchone()[0]
    total_ops = total_tx + total_err
    success_rate = round(total_tx / total_ops * 100.0, 1) if total_ops > 0 else 100.0
    conn.close()
    return {
        "total_transactions": total_tx,
        "total_errors":       total_err,
        "success_rate":       success_rate,
        "mean_latency_ms":    round(mean_latency, 1) if mean_latency else None,
        "last_scan":          last_row[0] if last_row else None,
    }


@app.get("/shifts")
def shifts(limit: int = 20):
    """Return recent shift sessions with aggregated statistics."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT session_id, station_id, operator, started_at, ended_at,
                   parcel_count, error_count, success_rate, mean_latency_ms, status
            FROM sessions
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
        result = []
        for r in rows:
            result.append({
                "session_id":      r["session_id"],
                "station_id":      r["station_id"],
                "operator":        r["operator"],
                "started_at":      r["started_at"],
                "ended_at":        r["ended_at"],
                "parcel_count":    r["parcel_count"],
                "error_count":     r["error_count"],
                "success_rate":    round(r["success_rate"] or 0.0, 1),
                "mean_latency_ms": round(r["mean_latency_ms"] or 0.0, 1),
                "status":          r["status"],
            })
        return result
    except Exception as e:
        return {"error": str(e), "detail": "sessions table may not exist yet"}
    finally:
        conn.close()


@app.get("/shifts/current")
def shifts_current():
    """Return the currently open session stats."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM sessions WHERE status='open' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {"status": "no_active_session"}
        return {
            "session_id":      row["session_id"],
            "station_id":      row["station_id"],
            "operator":        row["operator"],
            "started_at":      row["started_at"],
            "parcel_count":    row["parcel_count"],
            "error_count":     row["error_count"],
            "success_rate":    round(row["success_rate"] or 0.0, 1),
            "mean_latency_ms": round(row["mean_latency_ms"] or 0.0, 1),
            "status":          row["status"],
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


@app.get("/latest")
def latest():
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM transactions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return {}
    return _row_to_transaction(row)


@app.get("/history")
def history(limit: int = 50, offset: int = 0):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM transactions ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    conn.close()
    return [_row_to_transaction(r) for r in rows]


@app.get("/transactions")
def transactions_alias(limit: int = 50, offset: int = 0):
    """Alias for /history — served for frontend compatibility (Agent 4 frontend uses /transactions)."""
    return history(limit=limit, offset=offset)


@app.get("/errors")
def get_errors(
    limit: int = 20,
    severity: str = None,
    component: str = None,
):
    conn = get_conn()
    clauses = []
    params = []
    if severity:
        clauses.append("severity = ?")
        params.append(severity)
    if component:
        clauses.append("component = ?")
        params.append(component)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    keys_row = conn.execute("PRAGMA table_info(errors)").fetchall()
    col_names = {r[1] for r in keys_row}
    select_extras = ""
    if "image_url" in col_names:
        select_extras += ", image_url"
    if "severity" in col_names:
        select_extras += ", severity"
    if "component" in col_names:
        select_extras += ", component"
    rows = conn.execute(
        f"SELECT id, error_type, awb, message, created_at, timestamp{select_extras} "
        f"FROM errors {where} ORDER BY id DESC LIMIT ?",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/session")
def session():
    conn = get_conn()
    rows = conn.execute(
        "SELECT event, detail, ts FROM session_stats ORDER BY id DESC LIMIT 50"
    ).fetchall()
    conn.close()
    return [{"event": r[0], "detail": r[1], "timestamp": r[2]} for r in rows]


@app.get("/dashboard")
def dashboard():
    conn = get_conn()
    total_tx  = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    total_err = conn.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
    latest_row = conn.execute(
        "SELECT * FROM transactions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return {
        "total_transactions": total_tx,
        "total_errors":       total_err,
        "latest":             _row_to_transaction(latest_row) if latest_row else None,
    }


@app.get("/metrics")
def metrics():
    conn = get_conn()
    row = conn.execute(
        "SELECT AVG(length), AVG(width), AVG(height), AVG(weight) FROM transactions"
    ).fetchone()
    conn.close()
    return {
        "avg_length": round(row[0], 2) if row[0] else 0,
        "avg_width":  round(row[1], 2) if row[1] else 0,
        "avg_height": round(row[2], 2) if row[2] else 0,
        "avg_weight": round(row[3], 2) if row[3] else 0,
    }


@app.get("/system")
def system():
    conn = get_conn()
    scans  = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    errors = conn.execute("SELECT COUNT(*) FROM errors").fetchone()[0]

    station_id = "STATION_01"
    developer_mode = None
    calibration_required = None
    calibration_locked = None
    try:
        row = conn.execute(
            "SELECT developer_mode, calibration_required, calibration_locked, "
            "station_name "
            "FROM system_state WHERE id=1"
        ).fetchone()
        if row:
            developer_mode = bool(row["developer_mode"]) if row["developer_mode"] is not None else None
            calibration_required = bool(row["calibration_required"])
            calibration_locked = bool(row["calibration_locked"])
            if row["station_name"]:
                station_id = row["station_name"]
    except sqlite3.OperationalError:
        # developer_mode column (or system_state table) missing on an
        # older DB file that hasn't run the Build 004 migration yet.
        pass

    conn.close()
    return {
        "station_id":   station_id,
        "database":     "connected",
        "api":          "online",
        "transactions": scans,
        "errors":       errors,
        "developer_mode":        developer_mode,
        "calibration_required":  calibration_required,
        "calibration_locked":    calibration_locked,
    }


@app.get("/settings")
def get_settings():
    """
    Return all editable settings backed by system_state:
    developer_mode, metrological_standard, strict_mode,
    allow_near_oos, station_name, operator_name.
    Returns null for any field not yet migrated in this DB.
    """
    conn = get_conn()
    result = {
        "developer_mode": None,
        "metrological_standard": None,
        "strict_mode": None,
        "allow_near_oos": None,
        "station_name": None,
        "operator_name": None,
    }
    try:
        row = conn.execute(
            "SELECT developer_mode, metrological_standard, strict_mode, "
            "allow_near_oos, station_name, operator_name "
            "FROM system_state WHERE id=1"
        ).fetchone()
        if row:
            result["developer_mode"] = bool(row["developer_mode"]) if row["developer_mode"] is not None else None
            result["metrological_standard"] = row["metrological_standard"]
            result["strict_mode"] = bool(row["strict_mode"]) if row["strict_mode"] is not None else None
            result["allow_near_oos"] = bool(row["allow_near_oos"]) if row["allow_near_oos"] is not None else None
            result["station_name"] = row["station_name"]
            result["operator_name"] = row["operator_name"]
    except sqlite3.OperationalError:
        pass
    conn.close()
    return result


@app.post("/settings")
def post_settings(body: dict):
    """
    Update any combination of settings fields.
    Accepted keys: developer_mode, metrological_standard, strict_mode,
    allow_near_oos, station_name, operator_name.
    The running orchestrator picks up developer_mode within ~5s via its
    background poll; other fields are read at next relevant operation.
    """
    ALLOWED = {
        "developer_mode", "metrological_standard", "strict_mode",
        "allow_near_oos", "station_name", "operator_name",
    }
    updates = {k: v for k, v in body.items() if k in ALLOWED}
    if not updates:
        raise HTTPException(
            status_code=400,
            detail=f"Body must include at least one of: {sorted(ALLOWED)}"
        )

    conn = get_conn()
    try:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values())
        # Coerce booleans to int for SQLite
        coerced = []
        for k, v in updates.items():
            if k in ("developer_mode", "strict_mode", "allow_near_oos"):
                coerced.append(int(bool(v)))
            else:
                coerced.append(v)
        cur = conn.execute(
            f"UPDATE system_state SET {set_clause} WHERE id=1",
            coerced,
        )
        conn.commit()
        if cur.rowcount == 0:
            conn.close()
            raise HTTPException(
                status_code=500,
                detail="system_state row (id=1) not found — DB not initialized yet."
            )
    except sqlite3.OperationalError as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))
    conn.close()
    return {
        "updated": list(updates.keys()),
        "note": "Persisted. The running orchestrator picks up developer_mode within ~5s.",
    }


@app.get("/error_breakdown")
def error_breakdown():
    conn = get_conn()
    rows = conn.execute(
        "SELECT error_type, COUNT(*) FROM errors GROUP BY error_type ORDER BY COUNT(*) DESC"
    ).fetchall()
    conn.close()
    return [{"error_type": r[0], "count": r[1]} for r in rows]


@app.get("/success_rate")
def success_rate():
    conn = get_conn()
    scans  = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    errors = conn.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
    conn.close()
    total_ops = scans + errors
    rate = round(scans / total_ops * 100.0, 2) if total_ops > 0 else 100.0
    return {
        "total_transactions": scans,
        "total_errors":       errors,
        "total_operations":   total_ops,
        "success_rate":       rate,
    }


@app.get("/weight_distribution")
def weight_distribution():
    conn = get_conn()
    row = conn.execute("""
        SELECT
            SUM(CASE WHEN weight <  1                  THEN 1 ELSE 0 END),
            SUM(CASE WHEN weight >= 1  AND weight < 5  THEN 1 ELSE 0 END),
            SUM(CASE WHEN weight >= 5  AND weight < 10 THEN 1 ELSE 0 END),
            SUM(CASE WHEN weight >= 10                 THEN 1 ELSE 0 END)
        FROM transactions
    """).fetchone()
    conn.close()
    return {"0-1kg": row[0] or 0, "1-5kg": row[1] or 0, "5-10kg": row[2] or 0, "10kg+": row[3] or 0}


@app.get("/billing")
def billing():
    conn = get_conn()
    row = conn.execute(
        "SELECT SUM(weight), SUM(charge_weight) FROM transactions"
    ).fetchone()
    conn.close()
    actual = row[0] or 0
    charge = row[1] or 0
    return {
        "actual_total_weight":     actual,
        "chargeable_total_weight": charge,
        "recovered_weight":        charge - actual,
    }


@app.get("/scanner")
def scanner():
    conn = get_conn()
    row = conn.execute(
        "SELECT created_at FROM transactions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return {"status": "offline"}
    try:
        last_scan = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        minutes   = (datetime.now() - last_scan).total_seconds() / 60
        return {
            "last_scan":           row[0],
            "minutes_since_scan":  round(minutes, 1),
            "status":              "online" if minutes < 5 else "offline",
        }
    except Exception:
        return {"last_scan": row[0], "status": "unknown"}


# ---------------------------------------------------------------------------
# Export / Backup / Calibration endpoints (Build 005)
# ---------------------------------------------------------------------------

def _build_export_filter(conn, date_from, date_to, date_from_time,
                          date_to_time, shift_id):
    """
    Shared WHERE-clause builder for /export/csv and /export/pdf.

    Extracted from the original /export/csv inline logic (Build 005)
    without changing its behaviour — date_from/date_to/shift_id alone
    produce byte-identical clauses/params to before. date_from_time /
    date_to_time are new optional additions (HH:MM) that only take
    effect when their corresponding date param is also supplied, so
    existing callers that never sent them are unaffected.

    shift_id matches sessions.started_at/ended_at, which are REAL
    (epoch) — correctly filtered against the transactions.timestamp
    REAL column. date_from/date_to are plain calendar dates entered by
    a human in the export modal — correctly filtered against
    transactions.created_at (TEXT, "YYYY-MM-DD HH:MM:SS"). These are
    two different input types intentionally matched to two different
    columns; this is not unified into one column.
    """
    clauses = []
    params = []
    if shift_id:
        sess = conn.execute(
            "SELECT started_at, ended_at FROM sessions WHERE session_id=?",
            (shift_id,)
        ).fetchone()
        if sess:
            clauses.append("timestamp >= ?")
            params.append(sess["started_at"])
            if sess["ended_at"]:
                clauses.append("timestamp <= ?")
                params.append(sess["ended_at"])
    if date_from:
        time_part = date_from_time if date_from_time else "00:00:00"
        if len(time_part) == 5:  # "HH:MM" -> "HH:MM:SS"
            time_part += ":00"
        clauses.append("created_at >= ?")
        params.append(f"{date_from} {time_part}")
    if date_to:
        if date_to_time:
            time_part = date_to_time
            if len(time_part) == 5:
                time_part += ":00"
            clauses.append("created_at <= ?")
            params.append(f"{date_to} {time_part}")
        else:
            # No time override: preserve the original /export/csv
            # semantics exactly (strictly-less-than end of day).
            clauses.append("created_at < ?")
            params.append(date_to + " 23:59:59")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


@app.get("/export/csv")
def export_csv(
    date_from: str = None,
    date_to: str = None,
    date_from_time: str = None,
    date_to_time: str = None,
    shift_id: str = None,
):
    """
    Export transactions as CSV.
    Filters: date_from / date_to (YYYY-MM-DD), optionally narrowed by
    date_from_time / date_to_time (HH:MM), or shift_id (session_id).
    No filters → all transactions.

    date_from_time / date_to_time are additive — omitting them preserves
    the exact original whole-day behaviour (00:00:00 / 23:59:59).
    """
    conn = get_conn()
    where, params = _build_export_filter(
        conn, date_from, date_to, date_from_time, date_to_time, shift_id
    )
    rows = conn.execute(
        f"SELECT * FROM transactions {where} ORDER BY id ASC",
        params,
    ).fetchall()
    conn.close()

    if not rows:
        raise HTTPException(status_code=404, detail="No transactions match the filter.")

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(dict(rows[0]).keys()))
    writer.writeheader()
    for r in rows:
        writer.writerow(dict(r))
    buf.seek(0)

    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = f"dws_export_{ts}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/export/pdf")
def export_pdf(
    date_from: str = None,
    date_to: str = None,
    date_from_time: str = None,
    date_to_time: str = None,
    shift_id: str = None,
):
    """
    Export transactions matching the given filter as a single multi-page
    PDF summary (approximately one page per transaction, with page
    numbers). Same filter semantics as /export/csv.
    """
    conn = get_conn()
    where, params = _build_export_filter(
        conn, date_from, date_to, date_from_time, date_to_time, shift_id
    )
    rows = conn.execute(
        f"SELECT * FROM transactions {where} ORDER BY id ASC",
        params,
    ).fetchall()
    conn.close()

    if not rows:
        raise HTTPException(status_code=404, detail="No transactions match the filter.")

    txn_dicts = [_row_to_transaction(r) for r in rows]
    pdf_bytes = pdf_export.build_summary_pdf(txn_dicts, CAPTURE_DIR, POINTCLOUD_DIR)

    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = f"dws_export_{ts}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/export/transaction/{tx_id}/pdf")
def export_transaction_pdf(tx_id: int):
    """
    Single-transaction PDF report (one page): header, measurements,
    parcel snapshot, point cloud filename/link, footer.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM transactions WHERE id = ?", (tx_id,)
    ).fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail=f"Transaction {tx_id} not found.")

    txn_dict = _row_to_transaction(row)
    pdf_bytes = pdf_export.build_transaction_pdf(txn_dict, CAPTURE_DIR, POINTCLOUD_DIR)

    awb = (txn_dict.get("awb") or "unknown").replace(" ", "_")
    filename = f"DWS_{tx_id}_{awb}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/backup")
def backup_db():
    """
    Trigger a DB backup. Copies dws_data.db into BACKUP_DIR with a
    timestamp suffix. Returns the backup filename.
    """
    try:
        from dws_ros2.paths import BACKUP_DIR
    except ImportError:
        BACKUP_DIR = os.path.join(os.path.expanduser("~"), "dws_backups")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"dws_backup_{ts}.db")
    try:
        shutil.copy2(DB_PATH, dst)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backup failed: {e}")
    return {"backup_file": dst, "timestamp": ts}


@app.get("/calibration/latest")
def calibration_latest():
    """
    Most recent calibration result: date, operator, reference box, accuracy.

    Gracefully degrades (returns {"available": False}) if the
    calibration_history table does not exist yet — e.g. a fresh
    database file queried before database_node.py has run its
    startup migration. Never crashes the request.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM calibration_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        # calibration_history table not created yet on this DB file.
        return {"available": False}
    finally:
        conn.close()
    if not row:
        return {"available": False}
    d = dict(row)
    d["available"] = True
    return d


# ---------------------------------------------------------------------------
# Compatibility endpoints — expose information that already exists
# elsewhere in the system (VERSION file, git history, /proc resource
# counters, auth_login_history audit trail, system_state pipeline flag)
# through a few lightweight, frontend-facing routes. None of these
# introduce new subsystems or new dependencies.
# ---------------------------------------------------------------------------

@app.get("/system/version")
def system_version():
    """
    Application version + git commit (if this is a git checkout) +
    build timestamp. Backed by the VERSION file and `git` itself —
    see reliability/diagnostic_report.py's _read_version_file() /
    _read_git_info(), reused here so the two never drift apart.
    """
    if not _RELIABILITY_AVAILABLE:
        return {
            "application_version": app.version,
            "version_file": {"raw": None, "note": "reliability package unavailable"},
            "git": {"available": False, "reason": "reliability package unavailable"},
        }
    version_file = _diagnostic_report._read_version_file()
    git_info = _diagnostic_report._read_git_info()
    return {
        "application_version": app.version,
        "version_file": version_file,
        "git": git_info,
        # build_timestamp: the git commit timestamp is the closest honest
        # answer to "when was this build made" available on a deployed
        # checkout; there is no separate build-stamping step in this
        # project, so we do not fabricate one.
        "build_timestamp": git_info.get("commit_timestamp") if git_info.get("available") else None,
    }


@app.get("/system/metrics")
def system_metrics():
    """
    Real runtime resource metrics — cpu_percent, memory, disk — read
    straight from /proc and shutil.disk_usage, matching watchdog.py's
    no-psutil approach (this codebase deliberately avoids psutil as a
    dependency; see reliability/diagnostic_report.py). No dummy values:
    if a probe fails it reports {"error": ...} for that section rather
    than a fabricated number.
    """
    if not _RELIABILITY_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Reliability package unavailable; cannot collect system metrics.",
        )
    resources = _diagnostic_report.collect_system_resources()
    return {
        "cpu_percent": resources.get("cpu_percent"),
        "cpu_count": resources.get("cpu_count"),
        "load_average": resources.get("load_average"),
        "memory": resources.get("memory"),
        "disk": resources.get("disk"),
        "uptime_seconds": resources.get("uptime_seconds"),
    }


@app.get("/logs")
def logs(limit: int = 200, severity: str | None = None, source: str | None = None):
    """
    Audit/event log feed, backed by auth_login_history — the one
    append-only event table this system already has (see
    database/auth_schema.sql). Returns [] if the table doesn't exist
    yet or is empty; never fabricates entries.

    `severity` filters on the `result` column ("success"/"failure";
    also accepts "error"/"info" as aliases since that's the
    vocabulary the HMI logs page uses). `source` is accepted for
    forward-compatibility with the HMI's generic log-source filter
    but every row from this table is source="auth".
    """
    conn = get_conn()
    try:
        where = []
        params: list = []
        if severity:
            sev = severity.lower()
            if sev in ("failure", "error", "critical"):
                where.append("result = 'failure'")
            elif sev in ("success", "info"):
                where.append("result = 'success'")
        if source and source.lower() != "auth":
            # Nothing else feeds this endpoint yet — an unrelated source
            # filter means "no rows", not an error.
            return {"logs": [], "count": 0}
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = conn.execute(
            f"SELECT id, timestamp, username, ip_address, action, result, detail "
            f"FROM auth_login_history {clause} "
            f"ORDER BY id DESC LIMIT ?",
            (*params, max(1, min(limit, 1000))),
        ).fetchall()
    except sqlite3.OperationalError:
        # auth_login_history not created yet on this DB file.
        return {"logs": [], "count": 0}
    finally:
        conn.close()

    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "timestamp": r["timestamp"],
            "severity": "error" if r["result"] == "failure" else "info",
            "source": "auth",
            "message": f"{r['action']} — {r['username']} ({r['result']})",
            "username": r["username"],
            "ip_address": r["ip_address"],
            "action": r["action"],
            "result": r["result"],
            "detail": r["detail"],
        })
    return {"logs": out, "count": len(out)}


def _set_pipeline_running(value: bool) -> None:
    """
    Idempotent write to system_state.pipeline_running (see Build 005.2
    migration in database_node.py). Does not redesign the orchestrator —
    this column exists purely so the HMI has a truthful flag to read;
    it is not itself consumed by run_dws.py's pipeline logic.
    """
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE system_state SET pipeline_running = ? WHERE id = 1",
            (1 if value else 0,),
        )
        conn.commit()
    except sqlite3.OperationalError as e:
        raise HTTPException(
            status_code=503,
            detail=f"system_state.pipeline_running not available yet: {e}",
        )
    finally:
        conn.close()


@app.post("/pipeline/start")
def pipeline_start():
    """Set system_state.pipeline_running = 1. Reporting only — see _set_pipeline_running()."""
    _set_pipeline_running(True)
    return {"pipeline_running": True}


@app.post("/pipeline/stop")
def pipeline_stop():
    """Set system_state.pipeline_running = 0. Reporting only — see _set_pipeline_running()."""
    _set_pipeline_running(False)
    return {"pipeline_running": False}


@app.post("/scale/tare")
def scale_tare():
    """
    Software tare is permanently and intentionally disabled.

    Legal-metrology requirement: a weighing instrument's zero
    reference must only be adjustable via its physical, sealed
    controls — never remotely. This route exists only so the HMI gets
    a clear, explicit answer instead of a 404. Do not implement a
    software tare here under any circumstance.
    """
    raise HTTPException(
        status_code=403,
        detail=(
            "Software tare is intentionally disabled. "
            "Use the physical tare button on the weighing scale."
        ),
    )


# ---------------------------------------------------------------------------
# File-serve routes
# ---------------------------------------------------------------------------

@app.get("/captures/{name}")
def serve_capture(name: str):
    """Serve a JPEG snapshot by filename."""
    # Sanitise — no path traversal
    if ".." in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = os.path.join(CAPTURE_DIR, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Capture not found")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/pointclouds/{name}")
def serve_pointcloud(name: str):
    """Serve a .ply point-cloud file as a binary download."""
    if ".." in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = os.path.join(POINTCLOUD_DIR, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Point cloud not found")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=name,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
