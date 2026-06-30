"""
heartbeat.py
============
Reliability layer — heartbeat protocol.

DWS runs as three OS processes (backend / API / HMI-static) and, inside
the backend process, nine SimBus nodes on independent threads (see
ARCHITECTURE.md SS4, SS15). A process can be alive (PID exists) while a
thread inside it is deadlocked or stuck in a blocking I/O call — `kill -0`
will not detect that. Heartbeats close that gap: every monitored
component writes a small, cheap "I am alive and my loop is turning"
record on a known interval, independent of whatever work it's doing.

Design:
  - Heartbeats are written to a single SQLite table (`reliability_heartbeats`)
    in the *same* database the rest of DWS already uses (`dws_ros2.paths.DB_PATH`),
    so no new daemon, port, or IPC channel is required, and the watchdog
    (a separate OS process) can read them without talking to the backend
    process directly.
  - Each beat carries a monotonic sequence number as well as a wall-clock
    timestamp, so a clock change on the host doesn't masquerade as a
    component recovering or freezing.
  - Writing a heartbeat is wrapped in try/except and is designed to be
    callable from inside any hot loop (scale poll, vision stream, FSM
    callback) without risking the calling component's own stability —
    a heartbeat failure must never raise into application code.
  - Reading is just as cheap: `HeartbeatReader.snapshot()` returns the
    latest beat for every known component in one query.

This module has no dependency on rclpy, FastAPI, or anything else
DWS-specific beyond `dws_ros2.paths` (best-effort import — falls back to
an explicit DB path if that import isn't available, e.g. when run from
outside the colcon workspace's PYTHONPATH).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("reliability.heartbeat")

# ---------------------------------------------------------------------------
# Known components. Kept as plain strings (not an enum) so the watchdog,
# health_manager, and any future component can all agree on the same
# vocabulary without a shared import being mandatory.
# ---------------------------------------------------------------------------

COMPONENT_VISION = "vision_node"
COMPONENT_BARCODE = "barcode_node"
COMPONENT_SCALE = "scale_node"
COMPONENT_DATABASE = "database_node"
COMPONENT_API = "api_server"
COMPONENT_HMI = "hmi_server"
COMPONENT_ORCHESTRATOR = "orchestrator_node"
COMPONENT_WATCHDOG = "watchdog"

ALL_COMPONENTS = (
    COMPONENT_VISION,
    COMPONENT_BARCODE,
    COMPONENT_SCALE,
    COMPONENT_DATABASE,
    COMPONENT_API,
    COMPONENT_HMI,
    COMPONENT_ORCHESTRATOR,
)

# Default timeout (seconds) before a missed heartbeat is considered a
# problem, per component. Components with naturally slower loops (sync
# worker style) get a longer allowance. Override via
# HeartbeatConfig(timeouts={...}) or the RELIABILITY_HEARTBEAT_TIMEOUTS
# env var (JSON object) at deploy time.
DEFAULT_TIMEOUT_SECONDS = {
    COMPONENT_VISION: 10.0,
    COMPONENT_BARCODE: 15.0,
    COMPONENT_SCALE: 8.0,
    COMPONENT_DATABASE: 10.0,
    COMPONENT_API: 15.0,
    COMPONENT_HMI: 15.0,
    COMPONENT_ORCHESTRATOR: 10.0,
}

DEFAULT_BEAT_INTERVAL_SECONDS = 3.0


def _resolve_db_path() -> str:
    override = os.environ.get("DWS_DB_PATH")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    try:
        from dws_ros2.paths import DB_PATH  # type: ignore
        return DB_PATH
    except Exception:
        home = os.path.expanduser("~")
        return os.path.join(home, "dws_runtime", "database", "dws_data.db")


def _load_timeout_overrides() -> dict:
    raw = os.environ.get("RELIABILITY_HEARTBEAT_TIMEOUTS")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {str(k): float(v) for k, v in parsed.items()}
    except Exception as e:
        log.warning(f"[heartbeat] Could not parse RELIABILITY_HEARTBEAT_TIMEOUTS: {e}")
    return {}


@dataclass
class HeartbeatConfig:
    db_path: str = field(default_factory=_resolve_db_path)
    beat_interval_seconds: float = DEFAULT_BEAT_INTERVAL_SECONDS
    timeouts: dict = field(default_factory=lambda: dict(DEFAULT_TIMEOUT_SECONDS))

    def __post_init__(self):
        overrides = _load_timeout_overrides()
        if overrides:
            self.timeouts.update(overrides)

    def timeout_for(self, component: str) -> float:
        return self.timeouts.get(component, 12.0)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reliability_heartbeats (
    component       TEXT PRIMARY KEY,
    seq             INTEGER NOT NULL DEFAULT 0,
    pid             INTEGER,
    status          TEXT,
    detail          TEXT,
    beat_at         REAL NOT NULL,
    beat_at_iso     TEXT,
    started_at      REAL,
    interval_hint   REAL
);

CREATE TABLE IF NOT EXISTS reliability_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    component       TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    detail          TEXT,
    occurred_at     REAL NOT NULL,
    occurred_at_iso TEXT
);

CREATE INDEX IF NOT EXISTS idx_reliability_events_time
    ON reliability_events(occurred_at);
"""


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_schema(db_path: Optional[str] = None) -> None:
    """Idempotent. Safe to call from every process at startup."""
    path = db_path or _resolve_db_path()
    conn = _connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def record_event(component: str, event_type: str, detail: str = "", db_path: Optional[str] = None) -> None:
    """
    Append an immutable entry to the reliability event log — used for
    crash/recovery history (consumed by diagnostic_report.py and
    GET /health/history). Never raises.
    """
    path = db_path or _resolve_db_path()
    try:
        conn = _connect(path)
        try:
            conn.executescript(_SCHEMA)
            now = time.time()
            conn.execute(
                """
                INSERT INTO reliability_events
                    (component, event_type, detail, occurred_at, occurred_at_iso)
                VALUES (?, ?, ?, ?, ?)
                """,
                (component, event_type, detail, now, _iso(now)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log.warning(f"[heartbeat] record_event failed for {component}/{event_type}: {e}")


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Writer — embedded inside a monitored component (vision_node, scale_node,
# api_server, etc). Cheap enough to call every loop iteration; internally
# throttles actual DB writes to `beat_interval_seconds` so a tight inner
# loop doesn't hammer SQLite.
# ---------------------------------------------------------------------------

class HeartbeatEmitter:
    """
    Usage (inside any long-running component):

        from reliability.heartbeat import HeartbeatEmitter, COMPONENT_SCALE
        hb = HeartbeatEmitter(COMPONENT_SCALE)
        hb.start()          # optional: spawns its own timer thread
        ...
        hb.beat(status="ok")          # call this from the real work loop
        ...
        hb.beat(status="error", detail="serial disconnected")

    If `start()` is never called, `beat()` still works as a plain
    call-it-yourself heartbeat — the background thread is only a
    convenience for components that don't already have a tight loop
    calling `beat()` on their own.
    """

    def __init__(
        self,
        component: str,
        config: Optional[HeartbeatConfig] = None,
        auto_interval_seconds: Optional[float] = None,
    ):
        self.component = component
        self.config = config or HeartbeatConfig()
        self.auto_interval_seconds = auto_interval_seconds or self.config.beat_interval_seconds
        self._seq = 0
        self._lock = threading.Lock()
        self._last_write = 0.0
        self._min_write_gap = max(0.5, self.config.beat_interval_seconds / 3)
        self._started_at = time.time()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_status = "starting"
        self._last_detail = ""
        ensure_schema(self.config.db_path)

    def start(self):
        """Spawn a background thread that beats automatically. Optional."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._auto_loop, daemon=True, name=f"heartbeat-{self.component}"
        )
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        record_event(self.component, "STOPPED", db_path=self.config.db_path)

    def _auto_loop(self):
        record_event(self.component, "STARTED", db_path=self.config.db_path)
        while self._running:
            self.beat(self._last_status, self._last_detail)
            time.sleep(self.auto_interval_seconds)

    def beat(self, status: str = "ok", detail: str = "") -> None:
        """
        Record liveness. Cheap to call frequently — actual DB writes are
        throttled to roughly `beat_interval_seconds`. Never raises.
        """
        self._last_status = status
        self._last_detail = detail
        now = time.time()
        with self._lock:
            if now - self._last_write < self._min_write_gap:
                return
            self._last_write = now
            self._seq += 1
            seq = self._seq

        try:
            conn = _connect(self.config.db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO reliability_heartbeats
                        (component, seq, pid, status, detail, beat_at, beat_at_iso,
                         started_at, interval_hint)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(component) DO UPDATE SET
                        seq=excluded.seq,
                        pid=excluded.pid,
                        status=excluded.status,
                        detail=excluded.detail,
                        beat_at=excluded.beat_at,
                        beat_at_iso=excluded.beat_at_iso,
                        interval_hint=excluded.interval_hint
                    """,
                    (
                        self.component, seq, os.getpid(), status, detail,
                        now, _iso(now), self._started_at, self.auto_interval_seconds,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            # A heartbeat write failure must never propagate into the
            # caller's own control flow (e.g. the scale poll loop).
            log.debug(f"[heartbeat] write failed for {self.component}: {e}")


# ---------------------------------------------------------------------------
# Reader — used by health_manager.py / watchdog.py (separate process).
# ---------------------------------------------------------------------------

@dataclass
class HeartbeatRecord:
    component: str
    seq: int
    pid: Optional[int]
    status: Optional[str]
    detail: Optional[str]
    beat_at: float
    beat_at_iso: Optional[str]
    started_at: Optional[float]
    interval_hint: Optional[float]
    age_seconds: float
    timed_out: bool


class HeartbeatReader:
    def __init__(self, config: Optional[HeartbeatConfig] = None):
        self.config = config or HeartbeatConfig()
        ensure_schema(self.config.db_path)

    def snapshot(self) -> dict:
        """
        Returns {component_name: HeartbeatRecord} for every component
        that has ever beaten. Components that have never beaten at all
        (e.g. not yet wired for heartbeats, or never started since boot)
        are simply absent from the dict — callers should treat "absent"
        as UNKNOWN, not as a failure, since absence of *any* row is
        different from a stale/missing row.
        """
        out = {}
        now = time.time()
        try:
            conn = _connect(self.config.db_path)
            try:
                rows = conn.execute("SELECT * FROM reliability_heartbeats").fetchall()
            finally:
                conn.close()
        except Exception as e:
            log.warning(f"[heartbeat] snapshot read failed: {e}")
            return out

        for row in rows:
            component = row["component"]
            beat_at = row["beat_at"] or 0.0
            age = max(0.0, now - beat_at)
            timeout = self.config.timeout_for(component)
            out[component] = HeartbeatRecord(
                component=component,
                seq=row["seq"],
                pid=row["pid"],
                status=row["status"],
                detail=row["detail"],
                beat_at=beat_at,
                beat_at_iso=row["beat_at_iso"],
                started_at=row["started_at"],
                interval_hint=row["interval_hint"],
                age_seconds=round(age, 2),
                timed_out=age > timeout,
            )
        return out

    def get(self, component: str) -> Optional[HeartbeatRecord]:
        return self.snapshot().get(component)

    def recent_events(self, limit: int = 50, component: Optional[str] = None) -> list:
        try:
            conn = _connect(self.config.db_path)
            try:
                if component:
                    rows = conn.execute(
                        """
                        SELECT * FROM reliability_events
                        WHERE component = ?
                        ORDER BY id DESC LIMIT ?
                        """,
                        (component, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM reliability_events ORDER BY id DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
            finally:
                conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            log.warning(f"[heartbeat] recent_events read failed: {e}")
            return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cfg = HeartbeatConfig()
    print(f"[heartbeat] DB path: {cfg.db_path}")
    reader = HeartbeatReader(cfg)
    import json as _json
    snap = reader.snapshot()
    print(_json.dumps(
        {k: v.__dict__ for k, v in snap.items()}, indent=2, default=str
    ))
