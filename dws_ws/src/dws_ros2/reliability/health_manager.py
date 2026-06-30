"""
health_manager.py
==================
Reliability layer — central health state manager.

This is the single source of truth for "what is the state of every
subsystem right now". It does not itself probe hardware or processes —
that is watchdog.py's job (process/resource liveness) and the existing
in-process `health_monitor.py` / DB `health_status` row's job (camera,
scale, disk, calibration). `health_manager.py` *fuses* those signals —
process liveness (watchdog), heartbeat freshness (heartbeat.py), and the
existing DB-level health snapshot (health_status table) — into one
coherent state machine per component, with hysteresis so a single missed
check doesn't flap the reported state.

State model
-----------
    UNKNOWN   -> no signal has ever been observed for this component
    STARTING  -> first signal(s) observed, not yet enough samples to
                 trust (grace period after process start)
    ONLINE    -> healthy: process alive, heartbeat fresh, last check ok
    WARNING   -> degraded but functioning: e.g. one missed heartbeat,
                 high-but-not-critical resource usage, or a soft check
                 failure that the component itself can plausibly recover
                 from without intervention
    ERROR     -> hard failure observed (process dead, heartbeat timeout,
                 repeated check failures) — this is the signal
                 recovery_manager.py acts on
    OFFLINE   -> deliberately stopped (e.g. during planned shutdown) —
                 distinguished from ERROR so dashboards/alerts don't
                 page someone for an intentional stop

Transitions use small integer counters (`_consecutive_bad`,
`_consecutive_good`) rather than reacting to a single sample, matching
the same "don't flap on one bad reading" philosophy already used by
ScaleNode's stability algorithm in this codebase.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

log = logging.getLogger("reliability.health_manager")


class HealthState(str, Enum):
    ONLINE = "ONLINE"
    STARTING = "STARTING"
    WARNING = "WARNING"
    ERROR = "ERROR"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"


# Components this manager tracks. Matches heartbeat.py's vocabulary plus
# the two OS-process-only components (api_server, hmi_server) that don't
# map 1:1 onto a SimBus node.
COMPONENTS = (
    "vision_node",
    "barcode_node",
    "scale_node",
    "database_node",
    "api_server",
    "hmi_server",
    "orchestrator_node",
)

# How many consecutive bad samples before WARNING -> ERROR, and how many
# consecutive good samples before WARNING/ERROR -> ONLINE. Kept small
# (this manager is fed by watchdog's ~5s poll cycle, not a sub-second
# loop) so real failures are still caught within tens of seconds, not
# minutes.
BAD_SAMPLES_TO_ERROR = 3
GOOD_SAMPLES_TO_RECOVER = 2
STARTING_GRACE_SECONDS = 20.0


@dataclass
class ComponentHealth:
    name: str
    state: HealthState = HealthState.UNKNOWN
    reason: str = "no signal observed yet"
    last_change_at: float = field(default_factory=time.time)
    last_sample_at: Optional[float] = None
    consecutive_bad: int = 0
    consecutive_good: int = 0
    process_alive: Optional[bool] = None
    heartbeat_ok: Optional[bool] = None
    heartbeat_age_seconds: Optional[float] = None
    check_ok: Optional[bool] = None
    check_detail: Optional[str] = None
    cpu_percent: Optional[float] = None
    rss_mb: Optional[float] = None
    pid: Optional[int] = None
    deliberately_stopped: bool = False

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["state"] = self.state.value
        return d


class HealthManager:
    """
    Thread-safe in-memory + on-disk health state machine.

    health_api.py reads from this (via `snapshot()`), watchdog.py and
    other probes write into it (via `report_sample()`), and
    recovery_manager.py subscribes to state transitions (via
    `on_transition()`) to know when to act.

    Persisted to the `reliability_health_state` table on every change so
    a watchdog restart doesn't forget recent history (e.g. it should not
    re-announce "component just came ONLINE" for something that has
    actually been healthy for hours).
    """

    def __init__(self, db_path: Optional[str] = None):
        self._lock = threading.RLock()
        self._components: dict[str, ComponentHealth] = {
            name: ComponentHealth(name=name) for name in COMPONENTS
        }
        self._listeners: list[Callable[[str, HealthState, HealthState], None]] = []
        self._db_path = db_path or self._resolve_db_path()
        self._ensure_schema()
        self._load_persisted()

    # ------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------

    @staticmethod
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

    def _ensure_schema(self):
        import sqlite3
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reliability_health_state (
                    component       TEXT PRIMARY KEY,
                    state           TEXT NOT NULL,
                    reason          TEXT,
                    last_change_at  REAL,
                    updated_at      REAL,
                    payload_json    TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _load_persisted(self):
        """
        Best-effort restore of last-known state on startup, so a
        watchdog restart doesn't momentarily report everything as
        UNKNOWN while it warms back up. Persisted state older than
        STARTING_GRACE_SECONDS*4 is ignored as too stale to trust.
        """
        import sqlite3
        try:
            conn = sqlite3.connect(self._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("SELECT * FROM reliability_health_state").fetchall()
            finally:
                conn.close()
        except Exception as e:
            log.warning(f"[health_manager] could not load persisted state: {e}")
            return

        now = time.time()
        for row in rows:
            name = row["component"]
            if name not in self._components:
                continue
            if row["updated_at"] and (now - row["updated_at"]) > STARTING_GRACE_SECONDS * 4:
                continue
            try:
                payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            except Exception:
                payload = {}
            ch = self._components[name]
            try:
                ch.state = HealthState(row["state"])
            except Exception:
                ch.state = HealthState.UNKNOWN
            ch.reason = row["reason"] or ch.reason
            ch.last_change_at = row["last_change_at"] or now
            # `state`/`reason`/`last_change_at`/`name` are handled explicitly
            # above (state needs HealthState(...) coercion, not a raw
            # setattr) — skip them here so the generic payload restore
            # below doesn't clobber the enum with the plain string that
            # to_dict() serialized it as.
            _SKIP_KEYS = {"state", "reason", "last_change_at", "name"}
            for k, v in payload.items():
                if k in _SKIP_KEYS:
                    continue
                if hasattr(ch, k):
                    setattr(ch, k, v)

    def _persist(self, ch: ComponentHealth):
        import sqlite3
        try:
            conn = sqlite3.connect(self._db_path, timeout=5.0)
            try:
                conn.execute(
                    """
                    INSERT INTO reliability_health_state
                        (component, state, reason, last_change_at, updated_at, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(component) DO UPDATE SET
                        state=excluded.state, reason=excluded.reason,
                        last_change_at=excluded.last_change_at,
                        updated_at=excluded.updated_at,
                        payload_json=excluded.payload_json
                    """,
                    (
                        ch.name, ch.state.value, ch.reason, ch.last_change_at,
                        time.time(), json.dumps(ch.to_dict(), default=str),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            log.debug(f"[health_manager] persist failed for {ch.name}: {e}")

    # ------------------------------------------------------------
    # Listeners (recovery_manager hooks in here)
    # ------------------------------------------------------------

    def on_transition(self, callback: Callable[[str, HealthState, HealthState], None]):
        """Register `callback(component, old_state, new_state)`."""
        self._listeners.append(callback)

    def _fire_transition(self, name: str, old: HealthState, new: HealthState):
        for cb in list(self._listeners):
            try:
                cb(name, old, new)
            except Exception as e:
                log.exception(f"[health_manager] listener raised for {name}: {e}")

    # ------------------------------------------------------------
    # Reporting samples in
    # ------------------------------------------------------------

    def report_sample(
        self,
        component: str,
        process_alive: Optional[bool] = None,
        heartbeat_ok: Optional[bool] = None,
        heartbeat_age_seconds: Optional[float] = None,
        check_ok: Optional[bool] = None,
        check_detail: Optional[str] = None,
        cpu_percent: Optional[float] = None,
        rss_mb: Optional[float] = None,
        pid: Optional[int] = None,
        deliberately_stopped: Optional[bool] = None,
    ) -> ComponentHealth:
        """
        Feed one observation cycle for `component`. Called by
        watchdog.py roughly every poll interval. Any parameter left as
        None means "no opinion this cycle" — e.g. resource thresholds
        might not apply to every component.

        Returns the resulting ComponentHealth (post state-machine
        update) so the caller can log a transition immediately if it
        wants to, without a second lookup.

        `deliberately_stopped` follows the same None-means-no-opinion
        convention as every other parameter here: routine pollers
        (watchdog.py) should omit it entirely so they never clobber an
        operator's mark_deliberately_stopped(True) on the next cycle.
        """
        with self._lock:
            if component not in self._components:
                self._components[component] = ComponentHealth(name=component)
            ch = self._components[component]

            now = time.time()
            ch.last_sample_at = now
            if process_alive is not None:
                ch.process_alive = process_alive
            if heartbeat_ok is not None:
                ch.heartbeat_ok = heartbeat_ok
            if heartbeat_age_seconds is not None:
                ch.heartbeat_age_seconds = heartbeat_age_seconds
            if check_ok is not None:
                ch.check_ok = check_ok
            if check_detail is not None:
                ch.check_detail = check_detail
            if cpu_percent is not None:
                ch.cpu_percent = cpu_percent
            if rss_mb is not None:
                ch.rss_mb = rss_mb
            if pid is not None:
                ch.pid = pid
            if deliberately_stopped is not None:
                ch.deliberately_stopped = deliberately_stopped

            old_state = ch.state
            new_state, reason = self._evaluate(ch, now)

            if new_state != old_state:
                ch.state = new_state
                ch.reason = reason
                ch.last_change_at = now
                log.info(f"[health_manager] {component}: {old_state.value} -> {new_state.value} ({reason})")
                self._persist(ch)
                self._fire_transition(component, old_state, new_state)
            else:
                ch.reason = reason
                # still persist periodically so payload fields (cpu/rss/etc)
                # stay fresh on disk even without a state change
                self._persist(ch)

            return ch

    def _evaluate(self, ch: ComponentHealth, now: float) -> tuple[HealthState, str]:
        """
        Pure state-machine step. Returns (new_state, reason).
        Hysteresis lives in consecutive_bad/consecutive_good counters
        on the ComponentHealth itself.
        """
        if ch.deliberately_stopped:
            ch.consecutive_bad = 0
            ch.consecutive_good = 0
            return HealthState.OFFLINE, "deliberately stopped (planned)"

        # Hard failure signals — process dead is unambiguous.
        if ch.process_alive is False:
            ch.consecutive_bad += 1
            ch.consecutive_good = 0
            return HealthState.ERROR, "process not running"

        # Compose a single "this sample looked bad" boolean from
        # whichever signals are present this cycle.
        bad_signals = []
        if ch.heartbeat_ok is False:
            bad_signals.append(f"heartbeat stale ({ch.heartbeat_age_seconds}s)")
        if ch.check_ok is False:
            bad_signals.append(ch.check_detail or "health check failed")

        sample_bad = len(bad_signals) > 0

        # Newly-seen component, or one with no prior timestamp: give it
        # a grace period before holding it to ONLINE-or-ERROR account,
        # mirroring orchestrator boot sequencing — a process that just
        # started legitimately hasn't produced a heartbeat yet.
        in_grace = (
            ch.state in (HealthState.UNKNOWN, HealthState.STARTING)
            and ch.last_sample_at is not None
            and (now - (ch.last_change_at or now)) < STARTING_GRACE_SECONDS
        )

        if sample_bad:
            ch.consecutive_bad += 1
            ch.consecutive_good = 0
            if ch.consecutive_bad >= BAD_SAMPLES_TO_ERROR:
                return HealthState.ERROR, "; ".join(bad_signals)
            if in_grace:
                return HealthState.STARTING, "warming up; " + "; ".join(bad_signals)
            return HealthState.WARNING, "; ".join(bad_signals)

        # Sample looked good.
        ch.consecutive_good += 1
        ch.consecutive_bad = 0

        if ch.state in (HealthState.ERROR, HealthState.WARNING):
            if ch.consecutive_good >= GOOD_SAMPLES_TO_RECOVER:
                return HealthState.ONLINE, "recovered"
            return ch.state, f"recovering ({ch.consecutive_good}/{GOOD_SAMPLES_TO_RECOVER} good)"

        if ch.state in (HealthState.UNKNOWN, HealthState.STARTING):
            if in_grace and ch.consecutive_good < GOOD_SAMPLES_TO_RECOVER:
                return HealthState.STARTING, "warming up"
            return HealthState.ONLINE, "ok"

        return HealthState.ONLINE, "ok"

    def mark_deliberately_stopped(self, component: str, stopped: bool = True):
        """
        Called by recovery_manager.py / an operator-initiated stop so a
        planned shutdown reports OFFLINE instead of ERROR.
        """
        with self._lock:
            if component not in self._components:
                self._components[component] = ComponentHealth(name=component)
            self._components[component].deliberately_stopped = stopped
            if not stopped:
                # force re-evaluation on next sample rather than
                # snapping straight back to ONLINE
                self._components[component].state = HealthState.STARTING
                self._components[component].last_change_at = time.time()

    # ------------------------------------------------------------
    # Reading out
    # ------------------------------------------------------------

    def snapshot(self) -> dict:
        """Full health snapshot for every tracked component."""
        with self._lock:
            return {name: ch.to_dict() for name, ch in self._components.items()}

    def overall_state(self) -> HealthState:
        """
        Worst-of aggregation across all components, used for the
        top-level /health summary. ERROR beats WARNING beats
        STARTING/UNKNOWN beats ONLINE; OFFLINE components are excluded
        from the aggregate (a deliberately-stopped component shouldn't
        make the whole station look unhealthy).
        """
        with self._lock:
            states = [
                ch.state for ch in self._components.values()
                if ch.state != HealthState.OFFLINE
            ]
        if not states:
            return HealthState.UNKNOWN
        priority = [HealthState.ERROR, HealthState.WARNING, HealthState.STARTING, HealthState.UNKNOWN]
        for p in priority:
            if p in states:
                return p
        return HealthState.ONLINE

    def get(self, component: str) -> Optional[ComponentHealth]:
        with self._lock:
            ch = self._components.get(component)
            return ch


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    hm = HealthManager()
    hm.report_sample("scale_node", process_alive=True, heartbeat_ok=True, check_ok=True)
    print(json.dumps(hm.snapshot(), indent=2, default=str))
    print("overall:", hm.overall_state().value)
