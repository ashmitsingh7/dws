"""
database_node.py
================
Hosts:
- /dws/save_transaction
- /dws/save_error

Writes atomically to SQLite using WAL mode.
"""

import sqlite3
import threading
import time
import logging
import os

from dws_ros2.sim_bus import BUS
from dws_ros2.messages import (
    SaveTransactionRequest,
    SaveTransactionResponse
)

log = logging.getLogger("database_node")

try:
    from dws_ros2.paths import DB_PATH as _DEFAULT_DB_PATH
except ImportError:
    # Last-resort fallback if dws_ros2.paths isn't importable yet
    # (e.g. run completely standalone outside the package).
    _DEFAULT_DB_PATH = os.path.expanduser("~/dws_data.db")

DB_PATH = _DEFAULT_DB_PATH


class DatabaseNode:

    def __init__(
        self,
        db_path=DB_PATH
    ):

        self.db_path = db_path

        self._lock = threading.Lock()

        self._conn = None

        self._running = False

    # ---------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------

    def start(self):

        self._running = True

        # Build 006: ensure the database directory exists before opening
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        log.info(f"[db] Database path: {self.db_path}")

        self._conn = self._open_db()

        self._create_schema()
        self._migrate_schema()

        BUS.register_service(

            "/dws/save_transaction",

            self._handle_save

        )

        BUS.register_service(

            "/dws/save_error",

            self._handle_error

        )

        BUS.register_service(

            "/dws/get_calibration_boxes",

            lambda req: self.get_calibration_boxes()

        )

        BUS.register_service(

            "/dws/save_calibration_result",

            self.save_calibration_result

        )

        BUS.register_service(

            "/dws/get_system_state",

            lambda req: self.get_system_state()

        )

        BUS.register_service(

            "/dws/update_system_state",

            lambda req: self.update_system_state(**(req or {}))

        )

        BUS.register_service(

            "/dws/save_health_status",

            lambda req: self.save_health_status(**(req or {}))

        )

        BUS.register_service(

            "/dws/get_health_status",

            lambda req: self.get_health_status()

        )

        BUS.register_service(
            "/dws/open_session",
            lambda req: self.open_session(**(req or {}))
        )

        BUS.register_service(
            "/dws/close_session",
            lambda req: self.close_session(session_id=(req or {}).get("session_id", ""))
        )

        BUS.register_service(
            "/dws/update_session_on_transaction",
            lambda req: self.update_session_on_transaction(**(req or {}))
        )

        BUS.register_service(
            "/dws/get_sessions",
            lambda req: self.get_sessions(limit=(req or {}).get("limit", 20))
        )

        BUS.register_service(
            "/dws/get_current_session",
            lambda req: self.get_current_session()
        )

        log.info(

            f"DatabaseNode started "
            f"| db={self.db_path}"

        )

    def stop(self):

        self._running = False

        if self._conn:

            self._conn.close()

        log.info(

            "DatabaseNode stopped"

        )

    # ---------------------------------------------------------
    # Database setup
    # ---------------------------------------------------------

    def _open_db(self):

        conn = sqlite3.connect(

            self.db_path,

            check_same_thread=False,

            timeout=10.0

        )

        conn.row_factory = sqlite3.Row

        conn.execute(

            "PRAGMA journal_mode=WAL"

        )

        conn.execute(

            "PRAGMA synchronous=NORMAL"

        )

        conn.execute(

            "PRAGMA foreign_keys=ON"

        )

        return conn

    def _create_schema(self):

        with self._lock:

            self._conn.executescript("""

            CREATE TABLE IF NOT EXISTS transactions(

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                awb TEXT NOT NULL,

                tx_id TEXT,

                length REAL,

                width REAL,

                height REAL,

                weight REAL,

                object_count INTEGER DEFAULT 1,

                vol_weight REAL,

                charge_weight REAL,

                dim_factor REAL,

                station_id TEXT,

                image_url TEXT,

                pointcloud_url TEXT,

                timestamp REAL,

                created_at TEXT,

                sync_status INTEGER DEFAULT 0,

                confidence REAL DEFAULT 1.0

            );

            CREATE TABLE IF NOT EXISTS session_stats(

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                event TEXT,

                detail TEXT,

                ts REAL

            );

            CREATE TABLE IF NOT EXISTS sessions(

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                session_id TEXT UNIQUE,

                station_id TEXT,

                operator TEXT,

                started_at REAL,

                ended_at REAL,

                parcel_count INTEGER DEFAULT 0,

                error_count INTEGER DEFAULT 0,

                success_rate REAL DEFAULT 0.0,

                mean_latency_ms REAL DEFAULT 0.0,

                total_latency_ms REAL DEFAULT 0.0,

                status TEXT DEFAULT 'open'

            );

            CREATE TABLE IF NOT EXISTS errors(

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                error_type TEXT,

                awb TEXT,

                message TEXT,

                created_at TEXT,

                timestamp REAL,

                image_url TEXT,

                severity TEXT DEFAULT 'MEDIUM',

                component TEXT DEFAULT 'processing'

            );

            CREATE TABLE IF NOT EXISTS calibration_boxes(

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                awb TEXT UNIQUE NOT NULL,

                name TEXT,

                length_mm REAL,
                width_mm REAL,
                height_mm REAL,

                weight_kg REAL,

                tolerance_percent REAL DEFAULT 5,

                enabled INTEGER DEFAULT 1,

                created_at TEXT

            );

            CREATE TABLE IF NOT EXISTS calibration_history(

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                box_awb TEXT,

                pass_fail INTEGER,

                length_error_mm REAL,
                width_error_mm REAL,
                height_error_mm REAL,

                weight_error_kg REAL,

                count_error INTEGER,

                image_received INTEGER,

                confidence REAL,

                override_used INTEGER DEFAULT 0,

                operator TEXT DEFAULT '',

                created_at TEXT,

                timestamp REAL

            );

            CREATE TABLE IF NOT EXISTS system_state(

                id INTEGER PRIMARY KEY CHECK(id=1),

                calibration_required INTEGER DEFAULT 1,

                calibration_locked INTEGER DEFAULT 0,

                override_active INTEGER DEFAULT 0,

                shipments_since_calibration INTEGER DEFAULT 0,

                last_calibration_timestamp REAL DEFAULT 0,

                developer_mode INTEGER DEFAULT 1,

                metrological_standard TEXT DEFAULT 'Default',

                strict_mode INTEGER DEFAULT 1,

                allow_near_oos INTEGER DEFAULT 1,

                station_name TEXT DEFAULT 'STATION_01',

                operator_name TEXT DEFAULT '',

                pipeline_running INTEGER DEFAULT 0

            );

            CREATE TABLE IF NOT EXISTS health_status(

                id INTEGER PRIMARY KEY CHECK(id=1),

                overall_ok INTEGER DEFAULT 0,

                camera_ok INTEGER DEFAULT 0,
                camera_reason TEXT,

                scale_ok INTEGER DEFAULT 0,
                scale_reason TEXT,

                db_ok INTEGER DEFAULT 0,
                db_reason TEXT,

                disk_ok INTEGER DEFAULT 0,
                disk_free_gb REAL,
                disk_reason TEXT,

                calibration_ok INTEGER DEFAULT 0,
                calibration_reason TEXT,

                checked_at REAL DEFAULT 0,
                checked_at_iso TEXT

            );

            """)

            self._conn.commit()

            self._conn.execute(
                """
                INSERT OR IGNORE INTO system_state
                (
                    id,
                    calibration_required,
                    calibration_locked,
                    override_active,
                    shipments_since_calibration,
                    last_calibration_timestamp,
                    developer_mode,
                    metrological_standard,
                    strict_mode,
                    allow_near_oos,
                    station_name,
                    operator_name
                )
                VALUES
                (
                    1,
                    1,
                    0,
                    0,
                    0,
                    0,
                    1,
                    'Default',
                    1,
                    1,
                    'STATION_01',
                    ''
                )
                """
            )

            self._conn.execute(
                """
                INSERT OR IGNORE INTO health_status (id) VALUES (1)
                """
            )

            self._conn.commit()

        log.info(

            "Schema ready"

        )

    # ---------------------------------------------------------
    # Schema migration (for dws.db files created before
    # pointcloud_url was added to the transactions table)
    # ---------------------------------------------------------

    def _migrate_schema(self):

        with self._lock:

            cols = [
                row[1]
                for row in self._conn.execute(
                    "PRAGMA table_info(transactions)"
                ).fetchall()
            ]

            if "pointcloud_url" not in cols:

                log.info(
                    "[MIGRATION] Adding pointcloud_url "
                    "column to transactions table"
                )

                self._conn.execute(
                    "ALTER TABLE transactions "
                    "ADD COLUMN pointcloud_url TEXT"
                )

                self._conn.commit()

            if "latency_ms" not in cols:

                log.info(
                    "[MIGRATION] Adding latency_ms "
                    "column to transactions table"
                )

                self._conn.execute(
                    "ALTER TABLE transactions "
                    "ADD COLUMN latency_ms REAL DEFAULT 0"
                )

                self._conn.commit()

            if "operator" not in cols:

                log.info(
                    "[MIGRATION] Adding operator "
                    "column to transactions table"
                )

                self._conn.execute(
                    "ALTER TABLE transactions "
                    "ADD COLUMN operator TEXT DEFAULT ''"
                )

                self._conn.commit()

            if "confidence" not in cols:

                log.info(
                    "[MIGRATION] Adding confidence "
                    "column to transactions table"
                )

                self._conn.execute(
                    "ALTER TABLE transactions "
                    "ADD COLUMN confidence REAL DEFAULT 1.0"
                )

                self._conn.commit()

            # -------------------------------------------------
            # errors table — Build 005 columns
            # -------------------------------------------------

            error_cols = [
                row[1]
                for row in self._conn.execute(
                    "PRAGMA table_info(errors)"
                ).fetchall()
            ]

            if "image_url" not in error_cols:

                log.info(
                    "[MIGRATION] Adding image_url "
                    "column to errors table"
                )

                self._conn.execute(
                    "ALTER TABLE errors "
                    "ADD COLUMN image_url TEXT"
                )

                self._conn.commit()

            if "severity" not in error_cols:

                log.info(
                    "[MIGRATION] Adding severity "
                    "column to errors table"
                )

                self._conn.execute(
                    "ALTER TABLE errors "
                    "ADD COLUMN severity TEXT DEFAULT 'MEDIUM'"
                )

                self._conn.commit()

            if "component" not in error_cols:

                log.info(
                    "[MIGRATION] Adding component "
                    "column to errors table"
                )

                self._conn.execute(
                    "ALTER TABLE errors "
                    "ADD COLUMN component TEXT DEFAULT 'processing'"
                )

                self._conn.commit()

            # -------------------------------------------------
            # calibration_history table — Build 005 columns
            # -------------------------------------------------

            calhist_cols = [
                row[1]
                for row in self._conn.execute(
                    "PRAGMA table_info(calibration_history)"
                ).fetchall()
            ]

            if "operator" not in calhist_cols:

                log.info(
                    "[MIGRATION] Adding operator "
                    "column to calibration_history table"
                )

                self._conn.execute(
                    "ALTER TABLE calibration_history "
                    "ADD COLUMN operator TEXT DEFAULT ''"
                )

                self._conn.commit()

            # -------------------------------------------------
            # transactions — unique AWB (Build 005.1)
            # -------------------------------------------------

            txn_indexes = [
                row[1]
                for row in self._conn.execute(
                    "PRAGMA index_list(transactions)"
                ).fetchall()
            ]

            if "idx_transactions_awb" not in txn_indexes:

                log.info(
                    "[MIGRATION] Creating unique index on transactions.awb"
                )

                try:

                    self._conn.execute(
                        "CREATE UNIQUE INDEX idx_transactions_awb "
                        "ON transactions(awb)"
                    )

                    self._conn.commit()

                except sqlite3.IntegrityError as e:

                    log.error(
                        "[MIGRATION] Could not create unique AWB index "
                        f"(duplicate AWBs in DB?): {e}"
                    )

            state_cols = [
                row[1]
                for row in self._conn.execute(
                    "PRAGMA table_info(system_state)"
                ).fetchall()
            ]

            if "developer_mode" not in state_cols:

                log.info(
                    "[MIGRATION] Adding developer_mode "
                    "column to system_state table "
                    "(defaulting existing row to 1 — Developer "
                    "Mode is the sprint default per Build 004)"
                )

                self._conn.execute(
                    "ALTER TABLE system_state "
                    "ADD COLUMN developer_mode INTEGER DEFAULT 1"
                )

                self._conn.commit()

            # -------------------------------------------------
            # system_state — Build 005 Settings Page columns
            # -------------------------------------------------

            state_settings_defaults = (
                ("metrological_standard", "TEXT DEFAULT 'Default'"),
                ("strict_mode", "INTEGER DEFAULT 1"),
                ("allow_near_oos", "INTEGER DEFAULT 1"),
                ("station_name", "TEXT DEFAULT 'STATION_01'"),
                ("operator_name", "TEXT DEFAULT ''"),
            )

            for col_name, col_def in state_settings_defaults:

                if col_name not in state_cols:

                    log.info(
                        f"[MIGRATION] Adding {col_name} "
                        f"column to system_state table"
                    )

                    self._conn.execute(
                        f"ALTER TABLE system_state "
                        f"ADD COLUMN {col_name} {col_def}"
                    )

                    self._conn.commit()

            # -------------------------------------------------
            # system_state — pipeline_running (HMI truthful
            # pipeline state for POST /pipeline/start|stop)
            # -------------------------------------------------

            if "pipeline_running" not in state_cols:

                log.info(
                    "[MIGRATION] Adding pipeline_running "
                    "column to system_state table "
                    "(defaulting to 0 / stopped)"
                )

                self._conn.execute(
                    "ALTER TABLE system_state "
                    "ADD COLUMN pipeline_running INTEGER DEFAULT 0"
                )

                self._conn.commit()

    # ---------------------------------------------------------
    # Save transaction service
    # ---------------------------------------------------------

    def _handle_save(

        self,

        req: SaveTransactionRequest

    ):

        txn = req.transaction

        created_at = time.strftime(

            "%Y-%m-%d %H:%M:%S",

            time.localtime(

                txn.timestamp

            )

        )

        try:

            with self._lock:

                cur = self._conn.execute(

                    """
                    INSERT INTO transactions
                    (
                    awb,
                    tx_id,
                    length,
                    width,
                    height,
                    weight,
                    object_count,
                    vol_weight,
                    charge_weight,
                    dim_factor,
                    station_id,
                    image_url,
                    pointcloud_url,
                    timestamp,
                    created_at,
                    sync_status,
                    latency_ms,
                    operator,
                    confidence
                    )

                    VALUES
                    (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?
                    )
                    """,

                    (

                        txn.awb,
                        getattr(txn, "tx_id", None),
                        txn.length,
                        txn.width,
                        txn.height,
                        txn.weight,
                        getattr(txn, "object_count", 1),
                        txn.volumetric_weight,
                        txn.chargeable_weight,
                        txn.dim_factor,
                        txn.station_id,
                        txn.image_url,
                        getattr(txn, "pointcloud_url", "") or "",
                        txn.timestamp,
                        created_at,
                        getattr(txn, "latency_ms", 0.0) or 0.0,
                        getattr(txn, "operator", "") or "",
                        getattr(txn, "confidence", 1.0)

                    )

                )

                self._conn.commit()

            row_id = cur.lastrowid

            log.info(

                f"[db] SAVED "
                f"id={row_id} "
                f"awb={txn.awb}"

            )

            # Update live session counters
            current = self.get_current_session()
            if current:
                self.update_session_on_transaction(
                    current["session_id"],
                    latency_ms=getattr(txn, "latency_ms", 0.0) or 0.0
                )

            return SaveTransactionResponse(

                success=True,

                message="OK",

                row_id=row_id

            )

        except sqlite3.IntegrityError as e:

            log.error(

                f"[db] INSERT duplicate or constraint error:{e}"

            )

            return SaveTransactionResponse(

                success=False,

                message=str(e),

                row_id=-1

            )

        except sqlite3.Error as e:

            log.error(

                f"[db] INSERT error:{e}"

            )

            return SaveTransactionResponse(

                success=False,

                message=str(e),

                row_id=-1

            )

    # ---------------------------------------------------------
    # NEW:
    # Save error service
    # ---------------------------------------------------------

    def _handle_error(

        self,

        req

    ):

        try:

            created_at = time.strftime(

                "%Y-%m-%d %H:%M:%S",

                time.localtime(

                    req["timestamp"]

                )

            )

            with self._lock:

                cur = self._conn.execute(

                    """
                    INSERT INTO errors
                    (
                    error_type,
                    awb,
                    message,
                    created_at,
                    timestamp,
                    image_url,
                    severity,
                    component
                    )

                    VALUES
                    (
                    ?,?,?,?,?,?,?,?
                    )
                    """,

                    (

                        req["error_type"],
                        req["awb"],
                        req["message"],
                        created_at,
                        req["timestamp"],
                        req.get("image_path") or None,
                        req.get("severity", "MEDIUM"),
                        req.get("component", "processing")

                    )

                )

                self._conn.commit()

            log.warning(

                f"[db] ERROR SAVED "
                f"type={req['error_type']} "
                f"awb={req['awb']}"

            )

            current = self.get_current_session()
            if current:
                self.update_session_on_transaction(
                    current["session_id"],
                    latency_ms=0.0,
                    is_error=True,
                )

            return {

                "success": True,
                "row_id": cur.lastrowid

            }

        except Exception as e:

            log.error(

                f"[db] ERROR INSERT failed:{e}"

            )

            return {

                "success": False

            }

    # ---------------------------------------------------------
    # HMI history fetch
    # ---------------------------------------------------------

    def get_recent_transactions(

        self,

        limit=100

    ):

        with self._lock:

            cur = self._conn.execute(

                """
                SELECT *
                FROM transactions
                ORDER BY id DESC
                LIMIT ?
                """,

                (limit,)

            )

            rows = []

            for row in cur.fetchall():

                rows.append({

                    "awb": row["awb"],

                    "l": row["length"],

                    "w": row["width"],

                    "h": row["height"],

                    "wt": row["weight"],

                    "vol_wt": row["vol_weight"],

                    "charge_wt": row["charge_weight"],

                    "station": row["station_id"],

                    "ts": row["timestamp"],

                    "ts_str": row["created_at"],

                    "sync": row["sync_status"],

                    "fsm": "COMPLETE"

                })

            return rows

    # ---------------------------------------------------------
    # Existing helpers
    # ---------------------------------------------------------

    def get_unsynced(
        self,
        limit=50
    ):

        with self._lock:

            cur = self._conn.execute(

                """
                SELECT *
                FROM transactions
                WHERE sync_status=0
                LIMIT ?
                """,

                (limit,)

            )

            return cur.fetchall()

    def mark_synced(
        self,
        row_ids
    ):

        if not row_ids:
            return

        with self._lock:

            placeholders = ",".join(
                "?" * len(row_ids)
            )

            self._conn.execute(

                f"""
                UPDATE transactions
                SET sync_status=1
                WHERE id IN
                ({placeholders})
                """,

                row_ids

            )

            self._conn.commit()

    def get_all(self):

        with self._lock:

            cur = self._conn.execute(

                "SELECT * FROM transactions"

            )

            return cur.fetchall()

    def get_row_count(self):

        with self._lock:

            cur = self._conn.execute(

                "SELECT COUNT(*) FROM transactions"

            )

            return cur.fetchone()[0]

    # ---------------------------------------------------------
    # System state
    # ---------------------------------------------------------

    def get_system_state(self):

        with self._lock:

            cur = self._conn.execute(
                """
                SELECT *
                FROM system_state
                WHERE id=1
                """
            )

            row = cur.fetchone()

            return dict(row)

    # ---------------------------------------------------------
    # Health status (written by HealthMonitor, read by api_server)
    # ---------------------------------------------------------

    def save_health_status(self, **kwargs):
        """
        Upsert the single health_status row. Called by HealthMonitor
        (running in the same process as every other node) every
        `health.check_interval_seconds`. kwargs are any subset of the
        health_status columns.
        """

        if not kwargs:
            return

        with self._lock:

            fields = []
            values = []

            for k, v in kwargs.items():
                fields.append(f"{k}=?")
                values.append(v)

            values.append(1)

            self._conn.execute(
                f"""
                UPDATE health_status
                SET {",".join(fields)}
                WHERE id=?
                """,
                values
            )

            self._conn.commit()

    def get_health_status(self):

        with self._lock:

            cur = self._conn.execute(
                """
                SELECT *
                FROM health_status
                WHERE id=1
                """
            )

            row = cur.fetchone()

            return dict(row) if row else None

    # ---------------------------------------------------------
    # Session management (Build 003)
    # ---------------------------------------------------------

    def open_session(self, session_id: str, station_id: str = "", operator: str = "") -> dict:
        """Open a new shift session. Returns the session row as dict."""
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO sessions
                (session_id, station_id, operator, started_at, status)
                VALUES (?, ?, ?, ?, 'open')
                """,
                (session_id, station_id, operator, now)
            )
            self._conn.commit()
        log.info(f"[db] Session opened | id={session_id}")
        return {"session_id": session_id, "started_at": now}

    def close_session(self, session_id: str) -> dict:
        """Mark session closed using live counters already tracked in the sessions row."""
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            if not row:
                return {"error": "session not found"}

            self._conn.execute(
                "UPDATE sessions SET ended_at=?, status='closed' WHERE session_id=?",
                (now, session_id)
            )
            self._conn.commit()

        result = dict(row)
        log.info(
            f"[db] Session closed | id={session_id} "
            f"parcels={result['parcel_count']} errors={result['error_count']}"
        )
        return {
            "session_id":      session_id,
            "parcel_count":    result["parcel_count"],
            "error_count":     result["error_count"],
            "success_rate":    result["success_rate"],
            "mean_latency_ms": result["mean_latency_ms"],
        }

    def update_session_on_transaction(self, session_id: str, latency_ms: float, is_error: bool = False):
        """Increment live session counters when a transaction or error is saved."""
        with self._lock:
            if is_error:
                self._conn.execute(
                    """
                    UPDATE sessions
                    SET error_count = error_count + 1,
                        success_rate = CASE
                            WHEN (parcel_count + error_count + 1) > 0
                            THEN parcel_count * 100.0 / (parcel_count + error_count + 1)
                            ELSE 100.0 END
                    WHERE session_id=? AND status='open'
                    """,
                    (session_id,)
                )
            else:
                self._conn.execute(
                    """
                    UPDATE sessions
                    SET parcel_count = parcel_count + 1,
                        total_latency_ms = total_latency_ms + ?,
                        mean_latency_ms = (total_latency_ms + ?) / (parcel_count + 1),
                        success_rate = CASE
                            WHEN (parcel_count + 1 + error_count) > 0
                            THEN (parcel_count + 1) * 100.0 / (parcel_count + 1 + error_count)
                            ELSE 100.0 END
                    WHERE session_id=? AND status='open'
                    """,
                    (latency_ms, latency_ms, session_id)
                )
            self._conn.commit()

    def get_sessions(self, limit: int = 20) -> list:
        """Return recent sessions (closed and open), newest first."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT session_id, station_id, operator, started_at, ended_at,
                       parcel_count, error_count, success_rate, mean_latency_ms, status
                FROM sessions
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_current_session(self) -> dict | None:
        """Return the currently open session, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE status='open' ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def update_system_state(self, **kwargs):

        if not kwargs:
            return

        with self._lock:

            fields = []
            values = []

            for k, v in kwargs.items():

                fields.append(f"{k}=?")
                values.append(v)

            values.append(1)

            self._conn.execute(

                f"""
                UPDATE system_state
                SET {",".join(fields)}
                WHERE id=?
                """,

                values

            )

            self._conn.commit()

    # ---------------------------------------------------------
    # Calibration boxes
    # ---------------------------------------------------------

    def add_calibration_box(
        self,
        awb,
        name,
        length_mm,
        width_mm,
        height_mm,
        weight_kg,
        tolerance_percent=5
    ):

        with self._lock:

            self._conn.execute(
                """
                INSERT OR REPLACE INTO calibration_boxes
                (
                    awb,
                    name,
                    length_mm,
                    width_mm,
                    height_mm,
                    weight_kg,
                    tolerance_percent,
                    enabled,
                    created_at
                )
                VALUES
                (
                    ?,?,?,?,?,?,?,1,?
                )
                """,

                (
                    awb,
                    name,
                    length_mm,
                    width_mm,
                    height_mm,
                    weight_kg,
                    tolerance_percent,
                    time.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                )
            )

            self._conn.commit()

    def get_calibration_boxes(self):

        with self._lock:

            cur = self._conn.execute(
                """
                SELECT *
                FROM calibration_boxes
                WHERE enabled=1
                ORDER BY awb
                """
            )

            return [
                dict(r)
                for r in cur.fetchall()
            ]

    def delete_calibration_box(
        self,
        awb
    ):

        with self._lock:

            self._conn.execute(
                """
                DELETE
                FROM calibration_boxes
                WHERE awb=?
                """,
                (awb,)
            )

            self._conn.commit()

    # ---------------------------------------------------------
    # Calibration history
    # ---------------------------------------------------------

    def save_calibration_result(
        self,
        result
    ):

        with self._lock:

            self._conn.execute(
                """
                INSERT INTO calibration_history
                (
                    box_awb,
                    pass_fail,
                    length_error_mm,
                    width_error_mm,
                    height_error_mm,
                    weight_error_kg,
                    count_error,
                    image_received,
                    confidence,
                    override_used,
                    created_at,
                    timestamp,
                    operator
                )
                VALUES
                (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?
                )
                """,

                (
                    result["box_awb"],
                    result["pass_fail"],
                    result["length_error_mm"],
                    result["width_error_mm"],
                    result["height_error_mm"],
                    result["weight_error_kg"],
                    result["count_error"],
                    result["image_received"],
                    result["confidence"],
                    result["override_used"],
                    time.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    time.time(),
                    result.get("operator", "")
                )
            )

            self._conn.commit()

    # ---------------------------------------------------------
    # NEW: Filtered error query
    # ---------------------------------------------------------

    def get_errors_filtered(
        self,
        severity=None,
        component=None,
        limit=100
    ):
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
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, error_type, awb, message,
                       created_at, timestamp,
                       image_url, severity, component
                FROM errors
                {where}
                ORDER BY id DESC LIMIT ?
                """,
                params
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------
    # NEW: Database backup
    # ---------------------------------------------------------

    def backup_database(self):
        import shutil
        from dws_ros2.paths import BACKUP_DIR
        os.makedirs(BACKUP_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        src = self.db_path
        dst = os.path.join(BACKUP_DIR, f"dws_backup_{ts}.db")
        shutil.copy2(src, dst)
        log.info(f"[db] Backup written: {dst}")
        return dst


def main(args=None):

    logging.basicConfig(

        level=logging.INFO,

        format='[%(name)s][%(levelname)s] %(message)s'

    )

    node = None

    try:
        from dws_ros2.paths import resolve_db_path
        node = DatabaseNode(db_path=resolve_db_path())

        node.start()

        while True:

            time.sleep(1)

    except KeyboardInterrupt:

        pass

    finally:

        if node:

            node.stop()


if __name__ == "__main__":

    main()