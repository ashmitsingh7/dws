"""
watchdog.py
===========
Reliability layer — continuous monitor.

Runs as its own OS process (recommended: its own systemd unit,
`dws-watchdog@<user>.service` — see RELIABILITY_IMPLEMENTATION_GUIDE.md),
independent of the three processes it watches:

    - Backend   (ros2 run dws_ros2 run_dws)   — PID file: backend.pid
    - REST API  (uvicorn api_server:app)      — PID file: api.pid
    - HMI       (python3 -m http.server)      — PID file: hmi.pid

It is independent on purpose: a watchdog that lives inside the process
it's watching can die with it. This one reads the exact same PID files
`deploy/run.sh` already writes (`$DWS_RUN_DIR`, default `~/.dws/run/`),
so it requires zero changes to how DWS itself starts up.

Per poll cycle, for every monitored target, watchdog.py checks:
  1. dead process       — PID file points at a PID that's gone (`kill -0`
                           fails) → ERROR, immediate.
  2. frozen process     — PID alive but its heartbeat row in
                           reliability_heartbeats.py is older than that
                           component's configured timeout → ERROR. (A
                           process can be alive yet wedged on a syscall;
                           liveness alone can't detect that, heartbeat
                           freshness can.)
  3. excessive CPU      — sustained (not momentary) CPU% above threshold,
                           read from /proc/<pid>/stat deltas (no psutil
                           dependency, matches the Linux-only deployment
                           target documented in ARCHITECTURE.md SS14).
  4. excessive RAM      — RSS from /proc/<pid>/status above threshold.
  5. heartbeat timeout  — see (2); reported as its own signal too so
                           health_manager.py can distinguish "missed
                           heartbeat but process looks fine" (likely a
                           stuck thread, not a dead process) from a dead
                           process outright.

For the database, vision, scale, and barcode subsystems — which live
*inside* the single backend process rather than as separate OS
processes — watchdog.py cannot check "is the process alive" per
subsystem (they all share one PID). Instead it relies on:
  - each subsystem's own heartbeat row (closes the blind spot: backend
    PID alive doesn't mean ScaleNode's serial thread isn't wedged)
  - the existing `health_status` DB row written by health_monitor.py,
    consulted as a secondary, slower-cadence cross-check
  - direct probes for things with no in-process equivalent (DB file
    reachable via a raw SQLite connect+SELECT, independent of whether
    the backend process's own connection is healthy)

Every sample is reported into health_manager.HealthManager, which owns
the actual state machine / hysteresis. watchdog.py's job is sampling,
not deciding "is this an emergency" — that separation is what lets
recovery_manager.py subscribe to health_manager transitions without
caring how the underlying samples were taken.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

# Make this runnable both as `python3 reliability/watchdog.py` (script)
# and as `from reliability.watchdog import Watchdog` (imported module)
# without requiring the package to be pip-installed.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from heartbeat import (  # noqa: E402
    HeartbeatReader,
    HeartbeatConfig,
    record_event,
    COMPONENT_VISION,
    COMPONENT_BARCODE,
    COMPONENT_SCALE,
    COMPONENT_DATABASE,
    COMPONENT_API,
    COMPONENT_HMI,
    COMPONENT_ORCHESTRATOR,
)
from health_manager import HealthManager  # noqa: E402

log = logging.getLogger("reliability.watchdog")


# ---------------------------------------------------------------------------
# Path resolution — mirrors deploy/run.sh exactly so no env changes are
# needed to point the watchdog at the right files.
# ---------------------------------------------------------------------------

def _default_run_dir() -> str:
    return os.environ.get("DWS_RUN_DIR") or os.path.join(os.path.expanduser("~"), ".dws", "run")


def _default_log_dir() -> str:
    return os.environ.get("DWS_LOG_DIR") or os.path.join(os.path.expanduser("~"), ".dws", "logs")


def _default_db_path() -> str:
    override = os.environ.get("DWS_DB_PATH")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    try:
        from dws_ros2.paths import DB_PATH  # type: ignore
        return DB_PATH
    except Exception:
        return os.path.join(os.path.expanduser("~"), "dws_runtime", "database", "dws_data.db")


def _default_api_url() -> str:
    port = os.environ.get("DWS_API_PORT", "8000")
    return os.environ.get("DWS_API_URL") or f"http://127.0.0.1:{port}"


def _default_hmi_url() -> str:
    port = os.environ.get("DWS_HMI_STATIC_PORT", "8080")
    return os.environ.get("DWS_HMI_URL") or f"http://127.0.0.1:{port}"


@dataclass
class WatchdogConfig:
    run_dir: str = field(default_factory=_default_run_dir)
    log_dir: str = field(default_factory=_default_log_dir)
    db_path: str = field(default_factory=_default_db_path)
    api_url: str = field(default_factory=_default_api_url)
    hmi_url: str = field(default_factory=_default_hmi_url)

    poll_interval_seconds: float = 5.0

    # Sustained-CPU thresholds: a process must exceed cpu_percent_limit
    # for cpu_sustained_cycles consecutive polls before WARNING/ERROR is
    # raised — a single instantaneous spike (e.g. PE7 point-cloud export
    # mid-capture, per ARCHITECTURE.md SS15.4) is expected, not a fault.
    cpu_percent_warning: float = 85.0
    cpu_percent_error: float = 97.0
    cpu_sustained_cycles: int = 3

    rss_mb_warning: float = 2048.0
    rss_mb_error: float = 3584.0

    @property
    def backend_pid_file(self) -> str:
        return os.path.join(self.run_dir, "backend.pid")

    @property
    def api_pid_file(self) -> str:
        return os.path.join(self.run_dir, "api.pid")

    @property
    def hmi_pid_file(self) -> str:
        return os.path.join(self.run_dir, "hmi.pid")


# ---------------------------------------------------------------------------
# Low-level OS process probes — stdlib + /proc only, no psutil dependency.
# ---------------------------------------------------------------------------

def read_pid_file(path: str) -> Optional[int]:
    try:
        with open(path, "r") as f:
            raw = f.read().strip()
        return int(raw) if raw else None
    except (OSError, ValueError):
        return None


def pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but owned by another user — still alive.
        return True
    except OSError:
        return False


def read_proc_rss_mb(pid: int) -> Optional[float]:
    try:
        with open(f"/proc/{pid}/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    return round(kb / 1024.0, 1)
    except (OSError, ValueError, IndexError):
        return None
    return None


def read_proc_cpu_ticks(pid: int) -> Optional[tuple]:
    """
    Returns (utime+stime in clock ticks, wall_clock_time) for delta-based
    CPU% computation across two samples, same technique `top`/`ps` use
    against /proc/<pid>/stat fields 14 (utime) and 15 (stime).
    """
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            raw = f.read()
        # Fields after the comm field (which may contain spaces/parens)
        # start after the last ')'.
        after = raw.rsplit(")", 1)[-1].split()
        utime = int(after[11])  # field 14 overall, index 11 after split
        stime = int(after[12])  # field 15 overall
        return (utime + stime, time.time())
    except (OSError, ValueError, IndexError):
        return None


_CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


class CpuTracker:
    """Keeps the previous (ticks, wall_time) sample per PID to compute %CPU deltas."""

    def __init__(self):
        self._prev: dict[int, tuple] = {}
        self._sustained_bad: dict[int, int] = {}

    def sample(self, pid: int) -> Optional[float]:
        current = read_proc_cpu_ticks(pid)
        if current is None:
            return None
        prev = self._prev.get(pid)
        self._prev[pid] = current
        if prev is None:
            return None  # need two samples to compute a delta
        tick_delta = current[0] - prev[0]
        wall_delta = current[1] - prev[1]
        if wall_delta <= 0:
            return None
        cpu_percent = (tick_delta / _CLK_TCK) / wall_delta * 100.0
        return round(cpu_percent, 1)

    def note_sustained(self, pid: int, is_bad: bool) -> int:
        if is_bad:
            self._sustained_bad[pid] = self._sustained_bad.get(pid, 0) + 1
        else:
            self._sustained_bad[pid] = 0
        return self._sustained_bad[pid]

    def forget(self, pid: int):
        self._prev.pop(pid, None)
        self._sustained_bad.pop(pid, None)


# ---------------------------------------------------------------------------
# HTTP liveness probe (stdlib only — urllib, matching sync_worker_node.py's
# existing convention in this codebase rather than adding `requests` as a
# hard dependency).
# ---------------------------------------------------------------------------

def http_get_ok(url: str, timeout: float = 3.0) -> tuple:
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (200 <= resp.status < 300, f"HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        # A server that responds (even with an error code) is alive at
        # the TCP/HTTP layer — distinguish "answered with 5xx" from
        # "did not answer at all" (connection refused/timeout below).
        return (False, f"HTTP {e.code}")
    except Exception as e:
        return (False, f"UNREACHABLE:{type(e).__name__}:{e}")


def db_reachable(db_path: str, timeout: float = 3.0) -> tuple:
    try:
        conn = sqlite3.connect(db_path, timeout=timeout)
        try:
            conn.execute("SELECT 1").fetchone()
            return (True, None)
        finally:
            conn.close()
    except Exception as e:
        return (False, f"DB_UNREACHABLE:{e}")


def read_db_health_status_row(db_path: str) -> Optional[dict]:
    """
    Cross-check against the existing `health_status` row written by
    dws_ros2/health_monitor.py (in-process, 5s cycle). This gives the
    watchdog a second, independently-sourced opinion on camera/scale/
    calibration without re-implementing those real hardware probes.
    """
    try:
        conn = sqlite3.connect(db_path, timeout=3.0)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM health_status WHERE id=1").fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------

class Watchdog:
    def __init__(self, config: Optional[WatchdogConfig] = None, health_manager: Optional[HealthManager] = None):
        self.config = config or WatchdogConfig()
        self.health = health_manager or HealthManager(db_path=self.config.db_path)
        self.heartbeats = HeartbeatReader(
            HeartbeatConfig(db_path=self.config.db_path)
        )
        self._cpu = CpuTracker()
        self._running = False

    # ------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------

    def run_forever(self):
        self._running = True

        def _stop(signum, _frame):
            log.info(f"[watchdog] received signal {signum}, shutting down")
            self._running = False

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        log.info(
            f"[watchdog] starting — run_dir={self.config.run_dir} "
            f"db={self.config.db_path} interval={self.config.poll_interval_seconds}s"
        )
        record_event(COMPONENT_API, "WATCHDOG_STARTED", db_path=self.config.db_path)

        # First cycle establishes a CPU baseline (no %CPU yet); run it
        # immediately so steady-state polling starts right after.
        self.run_cycle_once()

        while self._running:
            time.sleep(self.config.poll_interval_seconds)
            if not self._running:
                break
            try:
                self.run_cycle_once()
            except Exception as e:
                log.exception(f"[watchdog] cycle failed: {e}")

        log.info("[watchdog] stopped")

    def run_cycle_once(self) -> dict:
        """One full poll cycle across every monitored OS process and
        in-process subsystem. Returns the resulting health snapshot."""
        os_process_results = {
            "backend": self._check_os_process(
                "backend", self.config.backend_pid_file,
                cpu_warn=self.config.cpu_percent_warning,
                cpu_err=self.config.cpu_percent_error,
            ),
            "api_server": self._check_os_process(
                "api_server", self.config.api_pid_file,
                cpu_warn=self.config.cpu_percent_warning,
                cpu_err=self.config.cpu_percent_error,
            ),
            "hmi_server": self._check_os_process(
                "hmi_server", self.config.hmi_pid_file,
                cpu_warn=self.config.cpu_percent_warning,
                cpu_err=self.config.cpu_percent_error,
            ),
        }

        backend_alive = os_process_results["backend"]["alive"]

        # api_server and hmi_server map 1:1 onto an OS process already
        # checked above — feed health_manager directly.
        self._report_simple_process(COMPONENT_API, os_process_results["api_server"])
        self._report_simple_process(COMPONENT_HMI, os_process_results["hmi_server"])

        # database_node: separate reachability probe (raw connect),
        # independent of whether the backend process's own connection
        # is wedged.
        db_ok, db_detail = db_reachable(self.config.db_path)
        self.health.report_sample(
            COMPONENT_DATABASE,
            process_alive=backend_alive if backend_alive is not None else None,
            check_ok=db_ok,
            check_detail=db_detail,
        )
        if not db_ok:
            record_event(COMPONENT_DATABASE, "DB_UNREACHABLE", db_detail or "", db_path=self.config.db_path)

        # In-process subsystems living inside the backend PID: vision,
        # barcode, scale, orchestrator. Liveness = backend process alive
        # AND that subsystem's own heartbeat fresh. Cross-checked
        # (best-effort) against the existing health_status DB row.
        legacy_health_row = read_db_health_status_row(self.config.db_path)
        beats = self.heartbeats.snapshot()

        for component in (COMPONENT_VISION, COMPONENT_BARCODE, COMPONENT_SCALE, COMPONENT_ORCHESTRATOR):
            self._report_in_process_subsystem(
                component, backend_alive, beats.get(component), legacy_health_row
            )

        return self.health.snapshot()

    # ------------------------------------------------------------
    # Per-target checks
    # ------------------------------------------------------------

    def _check_os_process(self, name: str, pid_file: str, cpu_warn: float, cpu_err: float) -> dict:
        pid = read_pid_file(pid_file)
        alive = pid_alive(pid)
        result = {
            "name": name, "pid": pid, "alive": alive,
            "cpu_percent": None, "rss_mb": None, "sustained_bad_cycles": 0,
        }

        if not alive:
            if pid is not None:
                self._cpu.forget(pid)
            return result

        result["rss_mb"] = read_proc_rss_mb(pid)
        cpu = self._cpu.sample(pid)
        result["cpu_percent"] = cpu

        is_bad = cpu is not None and cpu >= cpu_warn
        result["sustained_bad_cycles"] = self._cpu.note_sustained(pid, is_bad)
        return result

    def _report_simple_process(self, component: str, proc_result: dict):
        cfg = self.config
        alive = proc_result["alive"]
        cpu = proc_result["cpu_percent"]
        rss = proc_result["rss_mb"]
        sustained = proc_result["sustained_bad_cycles"]

        check_ok = True
        detail_parts = []

        if rss is not None and rss >= cfg.rss_mb_error:
            check_ok = False
            detail_parts.append(f"RSS {rss}MB >= error threshold {cfg.rss_mb_error}MB")
        elif rss is not None and rss >= cfg.rss_mb_warning:
            detail_parts.append(f"RSS {rss}MB elevated (warning threshold {cfg.rss_mb_warning}MB)")

        if cpu is not None and cpu >= cfg.cpu_percent_error and sustained >= cfg.cpu_sustained_cycles:
            check_ok = False
            detail_parts.append(f"CPU {cpu}% sustained {sustained} cycles >= error threshold {cfg.cpu_percent_error}%")
        elif cpu is not None and cpu >= cfg.cpu_percent_warning and sustained >= cfg.cpu_sustained_cycles:
            detail_parts.append(f"CPU {cpu}% sustained {sustained} cycles (warning threshold {cfg.cpu_percent_warning}%)")

        self.health.report_sample(
            component,
            process_alive=alive,
            check_ok=check_ok if alive else False,
            check_detail="; ".join(detail_parts) if detail_parts else None,
            cpu_percent=cpu,
            rss_mb=rss,
            pid=proc_result["pid"],
        )

        if not alive:
            record_event(component, "PROCESS_DEAD", f"pid_file pid={proc_result['pid']}", db_path=self.config.db_path)
        elif not check_ok:
            record_event(component, "RESOURCE_LIMIT", "; ".join(detail_parts), db_path=self.config.db_path)

    def _report_in_process_subsystem(self, component: str, backend_alive: bool, beat, legacy_row: Optional[dict]):
        heartbeat_ok = None
        heartbeat_age = None

        if beat is not None:
            heartbeat_ok = not beat.timed_out
            heartbeat_age = beat.age_seconds
        else:
            # No heartbeat row at all yet for this component. Don't treat
            # silence as failure immediately — the in-process emitters
            # may not have been wired in yet (Sprint 1 ships the
            # heartbeat *mechanism*; call sites inside vision_node.py /
            # scale_node.py / barcode_node.py / orchestrator_node.py are
            # wired per RELIABILITY_IMPLEMENTATION_GUIDE.md). Fall back
            # to the legacy DB health_status row where one applies.
            heartbeat_ok = None

        check_ok = None
        check_detail = None
        if legacy_row:
            if component == COMPONENT_VISION:
                check_ok = bool(legacy_row.get("camera_ok"))
                check_detail = legacy_row.get("camera_reason")
            elif component == COMPONENT_SCALE:
                check_ok = bool(legacy_row.get("scale_ok"))
                check_detail = legacy_row.get("scale_reason")

        self.health.report_sample(
            component,
            process_alive=backend_alive,
            heartbeat_ok=heartbeat_ok,
            heartbeat_age_seconds=heartbeat_age,
            check_ok=check_ok,
            check_detail=check_detail,
        )

        if heartbeat_ok is False:
            record_event(component, "HEARTBEAT_TIMEOUT", f"age={heartbeat_age}s", db_path=self.config.db_path)


def _setup_logging():
    log_dir = _default_log_dir()
    os.makedirs(log_dir, exist_ok=True)
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(os.path.join(log_dir, "watchdog.log")))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


def main():
    parser = argparse.ArgumentParser(description="DWS reliability watchdog")
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit (for diagnostics/cron)")
    parser.add_argument("--interval", type=float, default=None, help="Override poll interval (seconds)")
    args = parser.parse_args()

    _setup_logging()
    cfg = WatchdogConfig()
    if args.interval:
        cfg.poll_interval_seconds = args.interval

    wd = Watchdog(cfg)
    if args.once:
        snapshot = wd.run_cycle_once()
        import json
        print(json.dumps(snapshot, indent=2, default=str))
        return
    wd.run_forever()


if __name__ == "__main__":
    main()
