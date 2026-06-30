"""
diagnostic_report.py
=====================
Reliability layer — production diagnostic report.

A single function (`generate_report()`) that answers "what is the state
of this station, right now, in enough detail to triage a field issue
without remoting in." Used three ways:

  1. CLI:        `python3 diagnostic_report.py` — prints JSON to stdout,
                  or `--out report.json` to write to a file (useful for
                  a daily cron snapshot or attaching to a support ticket).
  2. HTTP:        health_api.py's `GET /diagnostics` calls this directly,
                  so the CLI and the API can never drift apart.
  3. Programmatic: anything else in the reliability layer (or a future
                  Electron "Export diagnostics" button) can import and
                  call `generate_report()` directly.

Every section is wrapped individually so one failing probe (e.g. no
RealSense camera attached) degrades that section to an honest error
message rather than crashing the whole report — a diagnostic tool that
can itself crash defeats its purpose.
"""

from __future__ import annotations

import glob
import json
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from heartbeat import HeartbeatReader, HeartbeatConfig  # noqa: E402
from health_manager import HealthManager  # noqa: E402
from watchdog import (  # noqa: E402
    WatchdogConfig,
    read_pid_file,
    pid_alive,
    read_proc_rss_mb,
    read_db_health_status_row,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(fn, *args, **kwargs):
    """Run `fn`, returning {"error": str(e)} instead of raising."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Version / build info
# ---------------------------------------------------------------------------

def _read_version_file() -> dict:
    """
    Reads the plain-text VERSION file shipped at the repo root
    (`DWS System v1.0 / Build: 005.1 / Date: ... / Status: ...`) — see
    ARCHITECTURE.md's own versioning convention. Falls back to whatever
    `dws_ros2.paths` can resolve relative to; never raises.
    """
    candidates = []
    try:
        from dws_ros2.paths import _SRC_CHECKOUT_ROOT  # type: ignore
        # _SRC_CHECKOUT_ROOT == .../dws_ws/src/dws_ros2 ; repo root is
        # three levels up (dws_ros2 -> src -> dws_ws -> repo root)
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(_SRC_CHECKOUT_ROOT)))
        candidates.append(os.path.join(repo_root, "VERSION"))
        candidates.append(os.path.join(_SRC_CHECKOUT_ROOT, "VERSION"))
    except Exception:
        pass
    candidates.append(os.environ.get("DWS_VERSION_FILE", ""))

    for path in candidates:
        if path and os.path.isfile(path):
            try:
                with open(path, "r") as f:
                    raw = f.read().strip()
                info = {"raw": raw}
                for line in raw.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        info[k.strip().lower().replace(" ", "_")] = v.strip()
                    elif line.lower().startswith("dws system"):
                        info["system"] = line.strip()
                return info
            except OSError:
                continue
    return {"raw": None, "note": "VERSION file not found"}


def _find_repo_root() -> str | None:
    """Resolve the repo root the same way _read_version_file() does."""
    try:
        from dws_ros2.paths import _SRC_CHECKOUT_ROOT  # type: ignore
        return os.path.dirname(os.path.dirname(os.path.dirname(_SRC_CHECKOUT_ROOT)))
    except Exception:
        return None


def _read_git_info() -> dict:
    """
    Best-effort git hash + commit timestamp for the deployed checkout.

    Deployments that install via a packaged .deb/.AppImage rather than
    a git clone will have no `.git` directory at all — that is a
    normal, expected case here, not an error. Returns
    {"available": False, ...} rather than raising or fabricating a
    hash.
    """
    repo_root = _find_repo_root()
    if not repo_root or not os.path.isdir(os.path.join(repo_root, ".git")):
        return {"available": False, "reason": "no .git directory in deployed checkout"}

    def _git(*args) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=3,
            )
            if out.returncode != 0:
                return None
            return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    commit_hash = _git("rev-parse", "HEAD")
    if commit_hash is None:
        return {"available": False, "reason": "git command unavailable or repo unreadable"}

    return {
        "available": True,
        "commit_hash": commit_hash,
        "commit_hash_short": _git("rev-parse", "--short", "HEAD"),
        "commit_timestamp": _git("log", "-1", "--format=%cI"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
    }


# ---------------------------------------------------------------------------
# System resources (CPU/RAM/disk) — /proc + shutil only, matching
# watchdog.py's no-psutil-dependency approach.
# ---------------------------------------------------------------------------

def _uptime_seconds() -> float | None:
    try:
        with open("/proc/uptime", "r") as f:
            return float(f.read().split()[0])
    except Exception:
        return None


def _load_average() -> dict | None:
    try:
        one, five, fifteen = os.getloadavg()
        return {"1m": round(one, 2), "5m": round(five, 2), "15m": round(fifteen, 2)}
    except (OSError, AttributeError):
        return None


def _cpu_count() -> int | None:
    return os.cpu_count()


def _read_proc_stat_total() -> tuple[int, int] | None:
    """
    Returns (busy_ticks, total_ticks) from the aggregate 'cpu' line in
    /proc/stat — user+nice+system+irq+softirq+steal vs. the full sum
    including idle. Two samples a short interval apart give a
    system-wide CPU% via the same delta technique as
    watchdog.read_proc_cpu_ticks(), just for the whole machine instead
    of one PID.
    """
    try:
        with open("/proc/stat", "r") as f:
            line = f.readline()
        parts = line.split()
        if not parts or parts[0] != "cpu":
            return None
        fields = [int(x) for x in parts[1:8]]  # user nice system idle iowait irq softirq
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        total = sum(fields)
        busy = total - idle
        return (busy, total)
    except (OSError, ValueError, IndexError):
        return None


def cpu_percent(sample_seconds: float = 0.1) -> float | None:
    """
    System-wide CPU utilization percent, sampled over `sample_seconds`.
    Blocking (briefly) by design — this is a diagnostics call, not a
    hot path. No psutil dependency, consistent with watchdog.py.
    """
    first = _read_proc_stat_total()
    if first is None:
        return None
    time.sleep(sample_seconds)
    second = _read_proc_stat_total()
    if second is None:
        return None
    busy_delta = second[0] - first[0]
    total_delta = second[1] - first[1]
    if total_delta <= 0:
        return None
    return round(busy_delta / total_delta * 100.0, 1)


def _meminfo() -> dict:
    info = {}
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) != 2:
                    continue
                key = parts[0].strip()
                val = parts[1].strip().split()[0]  # value in kB
                info[key] = int(val)
    except Exception as e:
        return {"error": str(e)}

    total_kb = info.get("MemTotal", 0)
    available_kb = info.get("MemAvailable", info.get("MemFree", 0))
    used_kb = max(0, total_kb - available_kb)
    return {
        "total_mb": round(total_kb / 1024, 1),
        "available_mb": round(available_kb / 1024, 1),
        "used_mb": round(used_kb / 1024, 1),
        "used_percent": round(used_kb / total_kb * 100, 1) if total_kb else None,
    }


def _disk_usage(paths: dict) -> dict:
    out = {}
    for label, path in paths.items():
        try:
            check_path = path
            if not os.path.isdir(check_path):
                check_path = os.path.dirname(check_path) or "/"
                while not os.path.isdir(check_path) and check_path != "/":
                    check_path = os.path.dirname(check_path) or "/"
            usage = shutil.disk_usage(check_path)
            out[label] = {
                "path": path,
                "total_gb": round(usage.total / (1024 ** 3), 2),
                "used_gb": round(usage.used / (1024 ** 3), 2),
                "free_gb": round(usage.free / (1024 ** 3), 2),
                "used_percent": round(usage.used / usage.total * 100, 1) if usage.total else None,
            }
        except Exception as e:
            out[label] = {"path": path, "error": str(e)}
    return out


def collect_system_resources() -> dict:
    paths_to_check = {"root": "/"}
    try:
        from dws_ros2.paths import RUNTIME_ROOT  # type: ignore
        paths_to_check["dws_runtime"] = RUNTIME_ROOT
    except Exception:
        pass

    return {
        "hostname": _safe(socket.gethostname),
        "platform": _safe(platform.platform),
        "python_version": platform.python_version(),
        "uptime_seconds": _uptime_seconds(),
        "load_average": _load_average(),
        "cpu_count": _cpu_count(),
        "cpu_percent": _safe(cpu_percent),
        "memory": _safe(_meminfo),
        "disk": _safe(_disk_usage, paths_to_check),
    }


# ---------------------------------------------------------------------------
# Hardware probes — camera, scale, barcode scanner. Each one is
# best-effort: a missing library or absent device is reported as a
# clean, structured fact, not a stack trace.
# ---------------------------------------------------------------------------

def _probe_camera() -> dict:
    try:
        import pyrealsense2 as rs  # type: ignore
    except ImportError:
        return {"available": False, "reason": "pyrealsense2 not importable"}
    try:
        ctx = rs.context()
        devices = list(ctx.query_devices())
        if not devices:
            return {"available": False, "reason": "no RealSense device detected"}
        info = []
        for d in devices:
            entry = {}
            try:
                entry["name"] = d.get_info(rs.camera_info.name)
            except Exception:
                entry["name"] = "unknown"
            try:
                entry["serial_number"] = d.get_info(rs.camera_info.serial_number)
            except Exception:
                pass
            try:
                entry["firmware_version"] = d.get_info(rs.camera_info.firmware_version)
            except Exception:
                pass
            info.append(entry)
        return {"available": True, "devices": info}
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


def _probe_calibration_files() -> dict:
    try:
        from dws_ros2.paths import CALIBRATION_DIR  # type: ignore
    except Exception:
        return {"checked": False, "reason": "dws_ros2.paths not importable"}

    required = ["stable_empty_depth.npy", "noise_map.npy", "system_geometry.npy"]
    present = {}
    for fname in required:
        path = os.path.join(CALIBRATION_DIR, fname)
        present[fname] = os.path.isfile(path)

    return {
        "checked": True,
        "directory": CALIBRATION_DIR,
        "files": present,
        "all_present": all(present.values()),
    }


def _probe_scale() -> dict:
    port = os.environ.get("DWS_SCALE_SERIAL_PORT", "/dev/ttyUSB0")
    try:
        from dws_ros2.config_loader import get_config  # type: ignore
        port = get_config().get("scale", {}).get("serial_port", port)
    except Exception:
        pass

    exists = os.path.exists(port)
    result = {"configured_port": port, "device_node_present": exists}
    if exists:
        try:
            import serial  # type: ignore
            with serial.Serial(port, timeout=0.2):
                result["openable"] = True
        except ImportError:
            result["openable"] = None
            result["reason"] = "pyserial not importable"
        except Exception as e:
            result["openable"] = False
            result["reason"] = f"{type(e).__name__}: {e}"
    return result


def _probe_barcode_scanner() -> dict:
    try:
        from evdev import InputDevice  # type: ignore
    except ImportError:
        return {"available": False, "reason": "evdev not importable"}

    supported_vendors = (
        "newland", "nls", "zebra", "symbol", "honeywell", "datalogic", "scanner",
    )
    found = []
    try:
        for path in glob.glob("/dev/input/event*"):
            try:
                dev = InputDevice(path)
                name = dev.name.lower()
                if any(v in name for v in supported_vendors):
                    found.append({"path": path, "name": dev.name})
            except Exception:
                continue
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}

    if found:
        return {"available": True, "devices": found}
    return {"available": False, "reason": "no matching HID scanner device found (manual/stdin fallback active)"}


def collect_hardware() -> dict:
    return {
        "camera": _safe(_probe_camera),
        "calibration": _safe(_probe_calibration_files),
        "scale": _safe(_probe_scale),
        "barcode_scanner": _safe(_probe_barcode_scanner),
    }


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def collect_database(db_path: str) -> dict:
    result = {"path": db_path, "exists": os.path.isfile(db_path)}
    if not result["exists"]:
        result["reason"] = "database file not found at resolved path"
        return result

    try:
        result["size_mb"] = round(os.path.getsize(db_path) / (1024 * 1024), 2)
    except OSError:
        pass

    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            result["reachable"] = True
            result["transaction_count"] = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            result["error_count"] = conn.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
            try:
                ss = conn.execute("SELECT * FROM system_state WHERE id=1").fetchone()
                result["system_state"] = dict(ss) if ss else None
            except sqlite3.OperationalError:
                result["system_state"] = None
            result["legacy_health_status"] = read_db_health_status_row(db_path)
        finally:
            conn.close()
    except Exception as e:
        result["reachable"] = False
        result["reason"] = f"{type(e).__name__}: {e}"

    return result


# ---------------------------------------------------------------------------
# Running processes (the three OS processes + their resource footprint)
# ---------------------------------------------------------------------------

def collect_processes(wd_cfg: WatchdogConfig) -> dict:
    targets = {
        "backend": wd_cfg.backend_pid_file,
        "api_server": wd_cfg.api_pid_file,
        "hmi_server": wd_cfg.hmi_pid_file,
    }
    out = {}
    for name, pid_file in targets.items():
        pid = read_pid_file(pid_file)
        alive = pid_alive(pid)
        entry = {"pid_file": pid_file, "pid": pid, "alive": alive}
        if alive and pid:
            entry["rss_mb"] = read_proc_rss_mb(pid)
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    entry["cmdline"] = f.read().replace(b"\x00", b" ").decode(errors="replace").strip()
            except OSError:
                pass
        out[name] = entry
    return out


# ---------------------------------------------------------------------------
# Health / heartbeat / recovery summary (reliability layer's own state)
# ---------------------------------------------------------------------------

def collect_reliability_state(db_path: str) -> dict:
    hm = HealthManager(db_path=db_path)
    reader = HeartbeatReader(HeartbeatConfig(db_path=db_path))
    return {
        "overall_status": hm.overall_state().value,
        "components": hm.snapshot(),
        "heartbeats": {k: v.__dict__ for k, v in reader.snapshot().items()},
    }


def collect_last_crashes(db_path: str, limit: int = 10) -> list:
    """
    'Crashes' here means recorded PROCESS_DEAD / ENTERED_ERROR /
    HEARTBEAT_TIMEOUT events from the reliability_events table — the
    closest thing to a crash log this system has without log
    aggregation infrastructure. Sourced from heartbeat.record_event call
    sites in watchdog.py.
    """
    interesting_types = {"PROCESS_DEAD", "ENTERED_ERROR", "HEARTBEAT_TIMEOUT", "DB_UNREACHABLE", "RESOURCE_LIMIT"}
    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM reliability_events ORDER BY id DESC LIMIT ?",
                (limit * 4,),  # over-fetch then filter, table may be mixed
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return []

    crashes = [dict(r) for r in rows if r["event_type"] in interesting_types]
    return crashes[:limit]


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def generate_report() -> dict:
    """
    Produce the complete diagnostic report. Every top-level section is
    independently fault-tolerant — a failure in one section never
    prevents the rest of the report from being generated.
    """
    wd_cfg = WatchdogConfig()

    report = {
        "generated_at": time.time(),
        "generated_at_iso": _now_iso(),
        "version": _safe(_read_version_file),
        "system": _safe(collect_system_resources),
        "hardware": _safe(collect_hardware),
        "database": _safe(collect_database, wd_cfg.db_path),
        "processes": _safe(collect_processes, wd_cfg),
        "reliability": _safe(collect_reliability_state, wd_cfg.db_path),
        "recent_events": _safe(collect_last_crashes, wd_cfg.db_path),
    }
    return report


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate a DWS production diagnostic report")
    parser.add_argument("--out", help="Write JSON report to this file instead of stdout")
    parser.add_argument("--pretty", action="store_true", default=True, help="Pretty-print JSON (default)")
    args = parser.parse_args()

    report = generate_report()
    text = json.dumps(report, indent=2, default=str)

    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
        print(f"Diagnostic report written to {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
