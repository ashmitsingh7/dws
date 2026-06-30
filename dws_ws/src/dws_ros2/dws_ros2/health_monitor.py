"""
health_monitor.py
==================

Closes the gap between "health checks exist" and "health checks are
exposed". `vision_node_realsense.VisionNodeRealSense.check_health()`
and `scale_node.ScaleNode.check_health()` already did real,
non-fabricated hardware checks — they were just only ever called
internally by the orchestrator FSM to gate acquisition, with the
result thrown away afterwards.

HealthMonitor runs in the same process as every other node (started
from run_dws.py, like ScaleNode/VisionNodeRealSense/etc.), polls all
five required signals on an interval, and writes the snapshot into
the `health_status` table via the existing DatabaseNode BUS services.
`api_server.py` (a separate process) then reads that row for an
honest, current `/health` response instead of a DB-ping-only stub.

Checks performed every cycle:
  - camera   -> vision_node.check_health()          (real frame probe)
  - scale    -> scale_node.check_health()            (real reading-age probe)
  - database -> a real SELECT against the live connection
  - disk     -> shutil.disk_usage() against the capture/DB volume
  - calibration -> VisionNodeRealSense.check_calibration_files()
                   (file presence + load + required-keys check)

Nothing here is mocked or assumed; every flag reflects an actual
check at the moment it was written, with a timestamp so a stale
result is visible if HealthMonitor itself dies.
"""

import os
import time
import shutil
import logging
import threading
from datetime import datetime, timezone

from dws_ros2.sim_bus import BUS
from dws_ros2.paths import CAPTURE_DIR

log = logging.getLogger("health_monitor")


class HealthMonitor:

    def __init__(
        self,
        vision_node,
        scale_node,
        db_node,
        check_interval_seconds=5,
        min_free_disk_gb=2.0,
        disk_check_path=None,
    ):
        self.vision_node = vision_node
        self.scale_node = scale_node
        self.db_node = db_node
        self.check_interval_seconds = check_interval_seconds
        self.min_free_disk_gb = min_free_disk_gb
        # Check the same volume captures/DB actually live on. Falls
        # back to CAPTURE_DIR's parent if it doesn't exist yet.
        self.disk_check_path = disk_check_path or CAPTURE_DIR

        self._running = False
        self._thread = None

        # last snapshot, kept in-memory too so other in-process
        # consumers (e.g. orchestrator) don't have to round-trip the DB
        self.latest = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="health_monitor"
        )
        self._thread.start()
        log.info(
            f"HealthMonitor started "
            f"(interval={self.check_interval_seconds}s)"
        )

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        log.info("HealthMonitor stopped")

    # ------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------

    def _run_loop(self):
        while self._running:
            try:
                self.run_check_once()
            except Exception as e:
                log.exception(f"Health check cycle failed: {e}")
            time.sleep(self.check_interval_seconds)

    def run_check_once(self):
        camera = self._check_camera()
        scale = self._check_scale()
        db = self._check_database()
        disk = self._check_disk()
        calibration = self._check_calibration()

        overall_ok = all([
            camera["ok"], scale["ok"], db["ok"], disk["ok"], calibration["ok"],
        ])

        now = time.time()
        snapshot = {
            "overall_ok": int(overall_ok),
            "camera_ok": int(camera["ok"]),
            "camera_reason": camera["reason"],
            "scale_ok": int(scale["ok"]),
            "scale_reason": scale["reason"],
            "db_ok": int(db["ok"]),
            "db_reason": db["reason"],
            "disk_ok": int(disk["ok"]),
            "disk_free_gb": disk["free_gb"],
            "disk_reason": disk["reason"],
            "calibration_ok": int(calibration["ok"]),
            "calibration_reason": calibration["reason"],
            "checked_at": now,
            "checked_at_iso": datetime.now(timezone.utc).isoformat(),
        }

        self.latest = snapshot

        try:
            BUS.call_service("/dws/save_health_status", snapshot, timeout=3.0)
        except Exception as e:
            log.warning(f"[HEALTH] Could not persist health snapshot: {e}")

        return snapshot

    # ------------------------------------------------------------
    # Individual checks — each delegates to the real check already
    # implemented on the relevant node where one exists.
    # ------------------------------------------------------------

    def _check_camera(self):
        if self.vision_node is None:
            return {"ok": False, "reason": "VISION_NODE_NOT_WIRED"}
        try:
            result = self.vision_node.check_health()
            if result.get("valid"):
                return {"ok": True, "reason": None}
            return {"ok": False, "reason": result.get("reason", "UNKNOWN")}
        except Exception as e:
            return {"ok": False, "reason": f"CHECK_EXCEPTION:{e}"}

    def _check_scale(self):
        if self.scale_node is None:
            return {"ok": False, "reason": "SCALE_NODE_NOT_WIRED"}
        try:
            result = self.scale_node.check_health()
            if result.get("valid"):
                return {"ok": True, "reason": None}
            return {"ok": False, "reason": result.get("reason", "UNKNOWN")}
        except Exception as e:
            return {"ok": False, "reason": f"CHECK_EXCEPTION:{e}"}

    def _check_database(self):
        if self.db_node is None or getattr(self.db_node, "_conn", None) is None:
            return {"ok": False, "reason": "DB_NOT_CONNECTED"}
        try:
            with self.db_node._lock:
                self.db_node._conn.execute("SELECT 1").fetchone()
            return {"ok": True, "reason": None}
        except Exception as e:
            return {"ok": False, "reason": f"DB_ERROR:{e}"}

    def _check_disk(self):
        try:
            path = self.disk_check_path
            if not os.path.isdir(path):
                # walk up to the nearest existing ancestor so a
                # not-yet-created captures dir doesn't break the check
                path = os.path.dirname(path) or "/"
                while not os.path.isdir(path) and path != "/":
                    path = os.path.dirname(path) or "/"
            usage = shutil.disk_usage(path)
            free_gb = round(usage.free / (1024 ** 3), 2)
            ok = free_gb >= self.min_free_disk_gb
            reason = None if ok else "LOW_DISK_SPACE"
            return {"ok": ok, "free_gb": free_gb, "reason": reason}
        except Exception as e:
            return {"ok": False, "free_gb": None, "reason": f"DISK_CHECK_EXCEPTION:{e}"}

    def _check_calibration(self):
        try:
            # Static — doesn't depend on a live vision_node instance,
            # so calibration is still checkable even if camera_ok is False.
            from dws_ros2.vision_node_realsense import VisionNodeRealSense
            result = VisionNodeRealSense.check_calibration_files()
            if result.get("valid"):
                return {"ok": True, "reason": None}
            return {"ok": False, "reason": result.get("reason", "UNKNOWN")}
        except Exception as e:
            return {"ok": False, "reason": f"CHECK_EXCEPTION:{e}"}
