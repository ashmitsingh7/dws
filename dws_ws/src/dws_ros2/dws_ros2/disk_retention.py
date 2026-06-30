"""
disk_retention.py
==================

Standalone disk-retention worker. Previously nothing on this
codebase ever deleted a capture or point cloud — every JPEG, depth
snapshot, and .ply file written by SnapshotManager/vision_node stayed
on disk forever, which on a single-station edge appliance with no
IT support eventually fills the drive and takes the whole system
down with it (exactly the failure mode Day 1 of the original plan
calls out).

DiskRetention runs as a daemon thread alongside the other nodes
(started from run_dws.py). On each cycle it:
  1. Walks CAPTURE_DIR and POINTCLOUD_DIR (from dws_ros2.paths).
  2. Deletes any file whose mtime is older than `retention_days`
     (from dws_params.yaml -> database.retention_days).
  3. Logs what it deleted and how much space was freed.

It does NOT touch the SQLite database itself — old transaction rows
remain forever; only the on-disk image/pointcloud artifacts referenced
by old transactions are pruned. The DB row stays gone-but-known (the
image_url/pointcloud_url will simply 404 if requested after the
retention window — that's the intended Phase 1 behaviour; a "delete
the file but keep a tombstone note" enhancement is straightforward to
add later if it's needed, but is out of scope here).

Safety:
  - Only deletes files (never directories) directly under the two
    configured roots — does not follow symlinks, does not recurse
    into unexpected paths, does not touch anything outside
    CAPTURE_DIR/POINTCLOUD_DIR.
  - A retention_days <= 0 disables deletion entirely (treated as
    "keep forever") rather than deleting everything by accident.
"""

import os
import time
import logging
import threading

from dws_ros2.paths import CAPTURE_DIR, POINTCLOUD_DIR

log = logging.getLogger("disk_retention")


class DiskRetention:

    def __init__(
        self,
        retention_days=30,
        check_interval_seconds=3600,
        directories=None,
    ):
        self.retention_days = retention_days
        self.check_interval_seconds = check_interval_seconds
        self.directories = directories or [CAPTURE_DIR, POINTCLOUD_DIR]

        self._running = False
        self._thread = None

        # stats from the most recent sweep, surfaced to /health or HMI if wanted
        self.last_sweep_at = None
        self.last_sweep_deleted = 0
        self.last_sweep_freed_bytes = 0

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="disk_retention"
        )
        self._thread.start()
        log.info(
            f"DiskRetention started "
            f"(retention_days={self.retention_days}, "
            f"interval={self.check_interval_seconds}s, "
            f"dirs={self.directories})"
        )

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        log.info("DiskRetention stopped")

    def _run_loop(self):
        # Run one sweep shortly after startup too, not just after the
        # first full interval — otherwise a freshly (re)started system
        # could run for hours before the very first cleanup.
        self.run_sweep_once()
        while self._running:
            time.sleep(self.check_interval_seconds)
            if not self._running:
                break
            try:
                self.run_sweep_once()
            except Exception as e:
                log.exception(f"Retention sweep failed: {e}")

    def run_sweep_once(self):
        if self.retention_days is None or self.retention_days <= 0:
            log.info(
                "[RETENTION] retention_days<=0 — retention disabled, "
                "keeping all captures/pointclouds"
            )
            self.last_sweep_at = time.time()
            self.last_sweep_deleted = 0
            self.last_sweep_freed_bytes = 0
            return

        cutoff = time.time() - (self.retention_days * 86400)
        deleted = 0
        freed_bytes = 0

        for directory in self.directories:
            if not os.path.isdir(directory):
                continue

            try:
                entries = os.listdir(directory)
            except OSError as e:
                log.warning(f"[RETENTION] Could not list {directory}: {e}")
                continue

            for name in entries:
                path = os.path.join(directory, name)

                try:
                    if not os.path.isfile(path) or os.path.islink(path):
                        continue
                    mtime = os.path.getmtime(path)
                    if mtime < cutoff:
                        size = os.path.getsize(path)
                        os.remove(path)
                        deleted += 1
                        freed_bytes += size
                except OSError as e:
                    log.warning(f"[RETENTION] Could not remove {path}: {e}")

        self.last_sweep_at = time.time()
        self.last_sweep_deleted = deleted
        self.last_sweep_freed_bytes = freed_bytes

        if deleted:
            log.info(
                f"[RETENTION] Deleted {deleted} file(s) older than "
                f"{self.retention_days}d, freed "
                f"{freed_bytes / (1024 * 1024):.1f} MB"
            )
        else:
            log.info(
                f"[RETENTION] Sweep complete — nothing older than "
                f"{self.retention_days}d found"
            )
