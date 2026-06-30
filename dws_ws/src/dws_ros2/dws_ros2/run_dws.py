"""
run_dws.py
==========

Single-process launcher for DWS system.

All nodes run inside one Python process
so they share the same SimBus instance.
"""

import time
import signal
import logging

from dws_ros2.config_loader import get_config
from dws_ros2.paths import resolve_db_path, ensure_dirs

from dws_ros2.barcode_node import BarcodeNode
from dws_ros2.scale_node import ScaleNode

# ---------------------------------------------------
# REALSENSE VISION NODE
# ---------------------------------------------------

from dws_ros2.vision_node_realsense import (
    VisionNodeRealSense
)

from dws_ros2.orchestrator_node import (
    OrchestratorNode
)

from dws_ros2.database_node import (
    DatabaseNode
)

from dws_ros2.hmi_bridge_node import (
    HmiBridgeNode
)

from dws_ros2.sync_worker_node import (
    SyncWorkerNode
)

from dws_ros2.health_monitor import (
    HealthMonitor
)

from dws_ros2.disk_retention import (
    DiskRetention
)


# ---------------------------------------------------
# LOGGING
# ---------------------------------------------------

logging.basicConfig(

    level=logging.INFO,

    format='[%(name)s][%(levelname)s] %(message)s'

)

log = logging.getLogger(
    "launcher"
)


def main():

    # ---------------------------------------------------
    # Build 006: create all runtime directories on startup
    # ---------------------------------------------------
    ensure_dirs()

    # ---------------------------------------------------
    # SIGTERM HANDLING
    # ---------------------------------------------------
    # Route SIGTERM into the same KeyboardInterrupt-driven
    # shutdown path used for Ctrl-C, so the finally: block
    # below (which stops every node in reverse order) runs
    # identically for either signal.

    def _handle_sigterm(signum, frame):
        log.info("SIGTERM received, shutting down")
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, _handle_sigterm)

    # ---------------------------------------------------
    # LOAD CONFIG  (dws_params.yaml, env overrides applied)
    # ---------------------------------------------------

    cfg = get_config()

    log.info(
        f"[CONFIG] station_id={cfg['system']['station_id']} "
        f"scale_port={cfg['scale']['serial_port']} "
        f"db={resolve_db_path()} "
        f"retention_days={cfg['database']['retention_days']}"
    )

    # ---------------------------------------------------
    # CREATE NODES
    # ---------------------------------------------------

    barcode = BarcodeNode()

    scale = ScaleNode(
        port=cfg["scale"]["serial_port"],
        baudrate=cfg["scale"]["baudrate"],
        station_id=cfg["system"]["station_id"],
        buffer_size=cfg["scale"]["buffer_size"],
        spread_threshold=cfg["scale"]["spread_threshold_kg"],
        std_dev_threshold=cfg["scale"]["std_dev_threshold_kg"],
        required_stable_windows=cfg["scale"]["required_stable_windows"],
        zero_threshold=cfg["scale"]["zero_threshold_kg"],
    )

    # ---------------------------------------------------
    # REALSENSE VISION NODE
    # ---------------------------------------------------

    vision = VisionNodeRealSense(
        fps=cfg["vision"]["fps"],
        camera_mode=cfg["vision"]["camera_mode"],
    )

    database = DatabaseNode(db_path=resolve_db_path())

    # ---------------------------------------------------
    # ORCHESTRATOR FSM
    # ---------------------------------------------------

    orchestrator = OrchestratorNode(

        scale_node=scale,

        vision_node=vision,

        station_id=cfg["system"]["station_id"]

    )

    # ---------------------------------------------------
    # HMI BRIDGE
    # IMPORTANT:
    # pass orchestrator reference
    # ---------------------------------------------------

    hmi = HmiBridgeNode(
        websocket_port=cfg["hmi"]["websocket_port"],
        http_port=cfg["hmi"]["http_port"],
        orchestrator=orchestrator,
    )

    # ---------------------------------------------------
    # CLOUD SYNC WORKER
    # ---------------------------------------------------

    sync_worker = SyncWorkerNode(
        db_node=database,
        cloud_url=cfg["sync"]["cloud_url"],
        poll_interval=cfg["sync"]["poll_interval_seconds"],
        enabled=cfg["sync"]["enabled"],
    )

    # ---------------------------------------------------
    # HEALTH MONITOR  (honest /health: camera, scale, db,
    # disk, calibration — see health_monitor.py)
    # ---------------------------------------------------

    health_monitor = HealthMonitor(

        vision_node=vision,
        scale_node=scale,
        db_node=database,
        check_interval_seconds=cfg["health"]["check_interval_seconds"],
        min_free_disk_gb=cfg["health"]["min_free_disk_gb"],

    )

    # ---------------------------------------------------
    # DISK RETENTION  (deletes captures/pointclouds older
    # than database.retention_days — see disk_retention.py)
    # ---------------------------------------------------

    disk_retention = DiskRetention(

        retention_days=cfg["database"]["retention_days"],

    )

    try:

        log.info(
            "Starting DWS nodes..."
        )

        # ---------------------------------------------------
        # INFRASTRUCTURE FIRST
        # ---------------------------------------------------

        database.start()

        hmi.start()

        log.info(
            f"HMI websocket: ws://localhost:{cfg['hmi']['websocket_port']} "
            f"| HTTP: http://localhost:{cfg['hmi']['http_port']}"
        )

        sync_worker.start()

        # ---------------------------------------------------
        # HEALTH MONITOR + DISK RETENTION
        # (depend on database being up, since both persist
        # state / prune files based on it)
        # ---------------------------------------------------

        health_monitor.start()

        disk_retention.start()

        # ---------------------------------------------------
        # HARDWARE NODES
        # ---------------------------------------------------

        scale.start()

        vision.start()

        # ---------------------------------------------------
        # MAIN ORCHESTRATOR FSM
        # ---------------------------------------------------

        orchestrator.start()

        # ---------------------------------------------------
        # BARCODE TRIGGER LAST
        # ---------------------------------------------------

        barcode.start()

        log.info(
            "All nodes started"
        )

        log.info(
            "DWS running in RealSense triggered acquisition mode"
        )

        # ---------------------------------------------------
        # MAIN LOOP
        # ---------------------------------------------------

        while True:

            time.sleep(1)

    except KeyboardInterrupt:

        log.info(
            "Keyboard interrupt received"
        )

    except Exception as e:

        log.exception(
            f"Fatal launcher error: {e}"
        )

    finally:

        log.info(
            "Stopping nodes..."
        )

        # ---------------------------------------------------
        # CLEAN SHUTDOWN
        # ---------------------------------------------------

        try:
            barcode.stop()
        except Exception:
            pass

        try:
            health_monitor.stop()
        except Exception:
            pass

        try:
            disk_retention.stop()
        except Exception:
            pass

        try:
            scale.stop()
        except Exception:
            pass

        try:
            vision.stop()
        except Exception:
            pass

        try:
            orchestrator.stop()
        except Exception:
            pass

        try:
            hmi.stop()
        except Exception:
            pass

        try:
            sync_worker.stop()
        except Exception:
            pass

        try:
            database.stop()
        except Exception:
            pass

        log.info(
            "Shutdown complete"
        )


if __name__ == "__main__":

    main()