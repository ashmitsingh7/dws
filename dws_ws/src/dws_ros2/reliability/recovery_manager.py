"""
recovery_manager.py
====================
Reliability layer — automatic recovery engine.

Subscribes to health_manager.HealthManager state transitions and, when a
component moves into ERROR, runs a recovery action for it with
exponential backoff between attempts. This is the module that actually
removes the operator from the loop: today, every failure mode documented
in ARCHITECTURE.md SS16.4 ("Recovery Behaviour") that isn't already
self-healing in-process (scale serial reconnect, scanner fallback) ends
in "operator must connect camera and restart" — this module is what
automates that "and restart."

Recovery levers available to a process that is NOT run_dws.py itself
----------------------------------------------------------------------
recovery_manager.py runs as its own OS process (same process as
watchdog.py, or standalone — see RELIABILITY_IMPLEMENTATION_GUIDE.md).
It has exactly two classes of lever, and is honest about the difference:

1. **OS-process-level** (real, immediate, always available): restart a
   dead/wedged OS process using the *exact same* entry points
   `deploy/run.sh` and the systemd unit already use — `run.sh restart`,
   or `systemctl restart dws@<user>` when a systemd unit is present and
   the watchdog detects it (mirrors the Electron-vs-systemd "don't
   double-launch" detection already specified in
   DWS_Productization_Analysis.md SS5.3). This is the lever for: backend
   dead, api_server dead, hmi_server dead, or backend wedged badly
   enough that nothing inside it (vision/scale/barcode/orchestrator) is
   heartbeating any more.

2. **In-process subsystem signal** (best-effort, NOT a guaranteed fix):
   for a subsystem stuck *inside a live backend process* (e.g. only
   ScaleNode's heartbeat has timed out, but the rest of the backend is
   fine), there is currently no IPC channel into a running `run_dws.py`
   process other than the DB poll pattern `orchestrator_node.py` already
   uses for `developer_mode` (5s poll loop, SS6.5 register_service
   table). recovery_manager.py writes a `reliability_recovery_requests`
   row that a future in-process poller could pick up — see
   RELIABILITY_IMPLEMENTATION_GUIDE.md "Sprint 2" for the actual
   consumer side of that hook, which is out of Sprint 1's scope per the
   task brief ("do not modify deployment scripts unless absolutely
   required", "do not redesign architecture"). Sprint 1 ships the
   request-table mechanism and escalates to a full backend restart if
   the targeted subsystem hasn't recovered within `escalate_after_seconds`
   — so a real fix happens either way, just via the process-level lever
   if the softer one isn't consumed in time.

Exponential backoff
--------------------
Per component: attempt delays double starting at `base_delay_seconds`
up to `max_delay_seconds`, and a `max_attempts` ceiling after which the
component is marked for operator attention (an event is recorded; this
module does not page/SMS anyone — that is out of Sprint 1's scope) and
automatic retries pause until either the component recovers on its own
or `reset_after_seconds` of sustained ONLINE elapses, at which point its
backoff state resets to start cleanly next time.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from heartbeat import record_event  # noqa: E402
from health_manager import HealthManager, HealthState  # noqa: E402
import watchdog as _wd_mod  # noqa: E402

log = logging.getLogger("reliability.recovery_manager")


# ---------------------------------------------------------------------------
# Which OS process "owns" each component, for the process-level lever.
# vision/barcode/scale/orchestrator all live inside the backend process.
# ---------------------------------------------------------------------------

COMPONENT_TO_OS_PROCESS = {
    "vision_node": "backend",
    "barcode_node": "backend",
    "scale_node": "backend",
    "orchestrator_node": "backend",
    "database_node": "backend",
    "api_server": "api_server",
    "hmi_server": "hmi_server",
}


def _default_run_sh() -> str:
    """
    Locate deploy/run.sh relative to the installed dws_ros2 package, with
    an environment override for non-standard layouts. Never hardcoded to
    a single user's home directory.
    """
    override = os.environ.get("DWS_RUN_SH")
    if override:
        return override
    try:
        from dws_ros2.paths import _PKG_DIR  # type: ignore
        candidate = os.path.join(_PKG_DIR, "..", "deploy", "run.sh")
        candidate = os.path.abspath(candidate)
        if os.path.isfile(candidate):
            return candidate
    except Exception:
        pass
    # Fallback: legacy layout used throughout this repo's own deploy docs.
    return os.path.join(os.path.expanduser("~"), "dws_ws", "src", "dws_ros2", "deploy", "run.sh")


def _systemd_unit_name() -> Optional[str]:
    """
    Returns the instance unit name (e.g. dws@operator.service) if one
    appears to be installed for the current user, else None. Mirrors the
    detection DWS_Productization_Analysis.md SS5.3 specifies for Electron
    so this module follows the same "don't double-supervise" contract.
    """
    override = os.environ.get("DWS_SYSTEMD_UNIT")
    if override:
        return override
    user = os.environ.get("USER") or os.environ.get("LOGNAME")
    if not user:
        return None
    unit = f"dws@{user}.service"
    try:
        result = subprocess.run(
            ["systemctl", "--user", "list-unit-files", unit],
            capture_output=True, text=True, timeout=5,
        )
        if unit in (result.stdout or ""):
            return unit
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["systemctl", "list-unit-files", unit],
            capture_output=True, text=True, timeout=5,
        )
        if unit in (result.stdout or ""):
            return unit
    except Exception:
        pass
    return None


@dataclass
class RecoveryConfig:
    base_delay_seconds: float = 5.0
    max_delay_seconds: float = 300.0
    max_attempts: int = 6
    reset_after_seconds: float = 600.0  # 10 min sustained ONLINE resets backoff
    # Don't even attempt an OS-process restart more often than this,
    # regardless of backoff math, to avoid restart storms.
    min_seconds_between_os_restarts: float = 20.0
    # If an in-process subsystem-only request hasn't been picked up /
    # hasn't resolved within this window, escalate to a full backend
    # restart rather than requesting forever.
    escalate_after_seconds: float = 90.0
    run_sh_path: str = field(default_factory=_default_run_sh)
    dry_run: bool = False  # if True, log what *would* happen, take no action


@dataclass
class _BackoffState:
    attempts: int = 0
    next_allowed_at: float = 0.0
    last_action_at: float = 0.0
    last_os_restart_at: float = 0.0
    request_issued_at: Optional[float] = None  # for in-process soft requests
    consecutive_online_since: Optional[float] = None


class RecoveryManager:
    def __init__(
        self,
        health_manager: HealthManager,
        config: Optional[RecoveryConfig] = None,
        db_path: Optional[str] = None,
    ):
        self.health = health_manager
        self.config = config or RecoveryConfig()
        self.db_path = db_path or HealthManager._resolve_db_path()
        self._state: dict[str, _BackoffState] = {}
        self._restarted_os_processes_this_cycle: set = set()
        self._ensure_schema()
        self.health.on_transition(self._on_transition)

    # ------------------------------------------------------------
    def _ensure_schema(self):
        import sqlite3
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reliability_recovery_requests (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    component       TEXT NOT NULL,
                    action          TEXT NOT NULL,
                    requested_at    REAL NOT NULL,
                    requested_at_iso TEXT,
                    consumed        INTEGER DEFAULT 0,
                    consumed_at     REAL,
                    detail          TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reliability_recovery_log (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    component       TEXT NOT NULL,
                    action          TEXT NOT NULL,
                    attempt         INTEGER,
                    outcome         TEXT,
                    detail          TEXT,
                    occurred_at     REAL NOT NULL,
                    occurred_at_iso TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _log_action(self, component, action, attempt, outcome, detail=""):
        import sqlite3
        from heartbeat import _iso
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            try:
                now = time.time()
                conn.execute(
                    """
                    INSERT INTO reliability_recovery_log
                        (component, action, attempt, outcome, detail, occurred_at, occurred_at_iso)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (component, action, attempt, outcome, detail, now, _iso(now)),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            log.warning(f"[recovery_manager] could not log action: {e}")

    def _request_in_process_recovery(self, component: str, action: str, detail: str = ""):
        import sqlite3
        from heartbeat import _iso
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            try:
                now = time.time()
                conn.execute(
                    """
                    INSERT INTO reliability_recovery_requests
                        (component, action, requested_at, requested_at_iso, detail)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (component, action, now, _iso(now), detail),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            log.warning(f"[recovery_manager] could not write recovery request: {e}")

    # ------------------------------------------------------------
    # Transition hook — fired synchronously by HealthManager whenever a
    # component's state changes. We only *act* on entry into ERROR;
    # WARNING is logged but not yet acted on (a component in WARNING is,
    # by health_manager's own hysteresis design, plausibly self-healing —
    # see health_manager.py's BAD_SAMPLES_TO_ERROR comment).
    # ------------------------------------------------------------

    def _on_transition(self, component: str, old_state: HealthState, new_state: HealthState):
        if new_state == HealthState.ONLINE:
            st = self._state.get(component)
            if st:
                st.consecutive_online_since = st.consecutive_online_since or time.time()
            return

        if new_state != HealthState.ERROR:
            return

        record_event(component, "ENTERED_ERROR", f"from {old_state.value}", db_path=self.db_path)
        self.attempt_recovery(component)

    # ------------------------------------------------------------
    # Core recovery attempt with exponential backoff
    # ------------------------------------------------------------

    def attempt_recovery(self, component: str) -> bool:
        """
        Attempt to recover `component` now, respecting backoff. Returns
        True if an action was actually taken this call, False if we're
        still within the backoff window or have hit max_attempts.
        """
        now = time.time()
        st = self._state.setdefault(component, _BackoffState())
        st.consecutive_online_since = None

        if now < st.next_allowed_at:
            log.debug(
                f"[recovery_manager] {component}: backoff active, "
                f"next attempt allowed at {st.next_allowed_at:.0f} (now {now:.0f})"
            )
            return False

        if st.attempts >= self.config.max_attempts:
            log.warning(
                f"[recovery_manager] {component}: max_attempts "
                f"({self.config.max_attempts}) reached — pausing automatic "
                f"recovery, operator attention needed"
            )
            record_event(
                component, "RECOVERY_EXHAUSTED",
                f"{st.attempts} attempts made", db_path=self.db_path,
            )
            return False

        st.attempts += 1
        st.last_action_at = now
        delay = min(
            self.config.max_delay_seconds,
            self.config.base_delay_seconds * (2 ** (st.attempts - 1)),
        )
        st.next_allowed_at = now + delay

        action = self._dispatch_recovery(component, st)

        self._log_action(component, action, st.attempts, "attempted")
        record_event(
            component, "RECOVERY_ATTEMPTED",
            f"action={action} attempt={st.attempts} next_backoff={delay}s",
            db_path=self.db_path,
        )
        return True

    def _dispatch_recovery(self, component: str, st: _BackoffState) -> str:
        owner_process = COMPONENT_TO_OS_PROCESS.get(component)

        if component in ("api_server", "hmi_server"):
            return self._restart_os_process(owner_process, st)

        if component in ("database_node",):
            # The DB itself isn't a process we own (it's a SQLite file),
            # but an unreachable DB usually means the backend process
            # that opened the connection (or holds a stale lock) needs
            # restarting. Try the in-process signal first only if the
            # backend otherwise looks alive; if database_node itself is
            # ERROR because the backend is also down, the backend check
            # below will already be driving a restart independently.
            return self._restart_os_process("backend", st, reason="database unreachable")

        if component in ("vision_node", "barcode_node", "scale_node", "orchestrator_node"):
            return self._recover_in_process_subsystem(component, st)

        # backend itself doesn't appear as a health_manager component
        # name directly (it's represented via api_server/hmi_server/etc
        # process checks plus the in-process subsystems above), but keep
        # a direct path available for completeness / future wiring.
        if component == "backend":
            return self._restart_os_process("backend", st)

        log.warning(f"[recovery_manager] no recovery action defined for component={component}")
        return "no_action_defined"

    # ------------------------------------------------------------
    # Lever 1: OS process restart
    # ------------------------------------------------------------

    def _restart_os_process(self, process_name: str, st: _BackoffState, reason: str = "") -> str:
        now = time.time()
        if now - st.last_os_restart_at < self.config.min_seconds_between_os_restarts:
            log.info(
                f"[recovery_manager] suppressing duplicate restart of "
                f"{process_name} (last restart {now - st.last_os_restart_at:.0f}s ago)"
            )
            return f"restart_suppressed:{process_name}"

        st.last_os_restart_at = now

        if self.config.dry_run:
            log.info(f"[recovery_manager] DRY RUN — would restart OS process: {process_name} ({reason})")
            return f"dry_run_restart:{process_name}"

        unit = _systemd_unit_name()
        if unit:
            action = f"systemctl_restart:{unit}"
            log.warning(f"[recovery_manager] restarting via systemd: {unit} (component process={process_name}, {reason})")
            try:
                subprocess.run(
                    ["sudo", "-n", "systemctl", "restart", unit],
                    capture_output=True, text=True, timeout=30, check=False,
                )
            except Exception as e:
                log.error(f"[recovery_manager] systemctl restart failed: {e}")
            self._log_action(process_name, action, st.attempts, "issued", reason)
            return action

        # No systemd unit detected — fall back to deploy/run.sh, which
        # owns the exact same three PID-file-tracked processes the
        # watchdog reads (see watchdog.py module docstring).
        action = f"run_sh_restart:{process_name}"
        log.warning(f"[recovery_manager] restarting via run.sh: {process_name} ({reason})")
        try:
            subprocess.run(
                [self.config.run_sh_path, "restart"],
                capture_output=True, text=True, timeout=60, check=False,
            )
        except Exception as e:
            log.error(f"[recovery_manager] run.sh restart failed: {e}")
        self._log_action(process_name, action, st.attempts, "issued", reason)
        return action

    # ------------------------------------------------------------
    # Lever 2: in-process subsystem soft request, escalating to a full
    # backend restart if unresolved within escalate_after_seconds.
    # ------------------------------------------------------------

    def _recover_in_process_subsystem(self, component: str, st: _BackoffState) -> str:
        now = time.time()
        if st.request_issued_at is None:
            st.request_issued_at = now
            action_map = {
                "vision_node": "reconnect_camera",
                "barcode_node": "reconnect_scanner",
                "scale_node": "reconnect_scale",
                "orchestrator_node": "reset_fsm",
            }
            action = action_map.get(component, "soft_recover")
            self._request_in_process_recovery(
                component, action,
                detail="requested by recovery_manager; consumed by an in-process "
                       "poller per RELIABILITY_IMPLEMENTATION_GUIDE.md Sprint 2",
            )
            log.warning(
                f"[recovery_manager] {component}: requested in-process recovery "
                f"({action}); will escalate to backend restart if unresolved "
                f"within {self.config.escalate_after_seconds}s"
            )
            return f"requested:{action}"

        elapsed = now - st.request_issued_at
        if elapsed >= self.config.escalate_after_seconds:
            log.warning(
                f"[recovery_manager] {component}: in-process request unresolved "
                f"after {elapsed:.0f}s — escalating to full backend restart"
            )
            st.request_issued_at = None
            return self._restart_os_process(
                "backend", st, reason=f"escalation from unresolved {component} request"
            )

        return f"awaiting_in_process_recovery:{component}:{elapsed:.0f}s_elapsed"

    # ------------------------------------------------------------
    # Backoff reset — called periodically (e.g. by the watchdog loop or
    # a small driver) once a component has been ONLINE long enough.
    # ------------------------------------------------------------

    def maybe_reset_backoff(self):
        now = time.time()
        for component, st in self._state.items():
            if st.consecutive_online_since is None:
                continue
            if now - st.consecutive_online_since >= self.config.reset_after_seconds:
                if st.attempts > 0:
                    log.info(
                        f"[recovery_manager] {component}: sustained ONLINE for "
                        f"{self.config.reset_after_seconds}s — resetting backoff state"
                    )
                st.attempts = 0
                st.next_allowed_at = 0.0
                st.request_issued_at = None

    def retry_persistent_failures(self):
        """
        `_on_transition` only fires once, on the edge into ERROR — a
        component that stays in ERROR across many poll cycles (the
        common case: dead process that hasn't been restarted yet, or a
        restart that didn't take) would otherwise only ever get a single
        recovery attempt. This sweeps every component still reporting
        ERROR and re-attempts recovery for any whose backoff window has
        elapsed. Call once per watchdog poll cycle (see run_supervised()).
        """
        for component, ch in self.health.snapshot().items():
            if ch.get("state") == HealthState.ERROR.value:
                self.attempt_recovery(component)

    def status(self) -> dict:
        return {
            component: {
                "attempts": st.attempts,
                "next_allowed_at": st.next_allowed_at,
                "last_action_at": st.last_action_at,
                "last_os_restart_at": st.last_os_restart_at,
                "request_pending": st.request_issued_at is not None,
            }
            for component, st in self._state.items()
        }


def run_supervised(poll_interval: float = 5.0, dry_run: bool = False):
    """
    Convenience entry point: runs a Watchdog + HealthManager +
    RecoveryManager together in one process. This is the recommended
    Sprint-1 deployment shape (see RELIABILITY_IMPLEMENTATION_GUIDE.md):
    one small supervisory process, independent of the three it watches,
    doing sampling -> state machine -> recovery in a single loop.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    cfg = _wd_mod.WatchdogConfig(poll_interval_seconds=poll_interval)
    hm = HealthManager(db_path=cfg.db_path)
    wd = _wd_mod.Watchdog(cfg, health_manager=hm)
    rm = RecoveryManager(hm, RecoveryConfig(dry_run=dry_run), db_path=cfg.db_path)

    log.info(f"[recovery_manager] supervised loop starting (dry_run={dry_run})")
    running = True

    import signal as _signal

    def _stop(signum, _frame):
        nonlocal running
        log.info(f"[recovery_manager] received signal {signum}, shutting down")
        running = False

    _signal.signal(_signal.SIGTERM, _stop)
    _signal.signal(_signal.SIGINT, _stop)

    while running:
        try:
            wd.run_cycle_once()
            rm.retry_persistent_failures()
            rm.maybe_reset_backoff()
        except Exception as e:
            log.exception(f"[recovery_manager] supervised cycle failed: {e}")
        time.sleep(cfg.poll_interval_seconds)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="DWS recovery manager (standalone or supervised)")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without taking them")
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()
    run_supervised(poll_interval=args.interval, dry_run=args.dry_run)
