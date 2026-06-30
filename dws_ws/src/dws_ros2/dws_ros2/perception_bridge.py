"""
perception_bridge.py
=====================

Thin integration layer between vision_node_realsense.py (live tracking /
locking, using the original realsense_vision engine) and the vendored
perception_engine v7 (dws_ros2.perception_engine), which is only invoked
ONCE per transaction, at the moment a parcel is locked.

Why a bridge instead of calling perception_engine directly from the node:
- Keeps vision_node_realsense.py's live loop untouched (no risk to the
  existing tracking/locking/human-rejection behavior).
- perception_engine.MeasurementEngine construction loads calibration from
  disk; we cache one instance and reuse it across transactions instead of
  reloading calibration files on every capture.
- Point cloud persistence (Open3D .ply write) is industrial-system glue,
  not measurement logic, so it belongs here rather than inside the engine.

Output contract (dict), always returned (never raises):
    {
        "success": bool,
        "failure_reason": str | None,
        "length_mm": float, "width_mm": float, "height_mm": float,
        "volume_mm3": float,
        "confidence": float,           # 0-100
        "point_count": int,
        "shape_type": str,             # "rectangular" | "irregular" | "unknown"
        "pointcloud_path": str | None, # absolute path to saved .ply, or None
        "processing_time_ms": float,
    }
"""

import os
import time
import logging

import numpy as np

from dws_ros2.perception_engine.measurement_engine import MeasurementEngine
from dws_ros2.perception_engine.capture.frame import Frame, Intrinsics
from dws_ros2.perception_engine.geometry.pointcloud import to_o3d
from dws_ros2.paths import POINTCLOUD_DIR as DEFAULT_POINTCLOUD_DIR

log = logging.getLogger("perception_bridge")

# Cached engine instance (calibration loaded once, reused per process)
_ENGINE = None
_ENGINE_CALIBRATION_DIR = None


def _get_engine(calibration_dir: str):
    global _ENGINE, _ENGINE_CALIBRATION_DIR

    if _ENGINE is None or _ENGINE_CALIBRATION_DIR != calibration_dir:
        log.info(f"[PERCEPTION_BRIDGE] Loading perception_engine v7 calibration from {calibration_dir}")
        _ENGINE = MeasurementEngine(
            calibration_dir=calibration_dir,
            use_temporal_filter=False,   # single-shot capture, no frame history
        )
        _ENGINE_CALIBRATION_DIR = calibration_dir

    return _ENGINE


def _build_frame(color_image, depth_raw_units, intrinsics_dict):
    """
    color_image: (H, W, 3) uint8 BGR
    depth_raw_units: (H, W) raw RealSense depth units (float32 or uint16,
        NOT yet scaled to meters) -- this is exactly what
        RealSenseCamera.get_frames()["depth"] already provides.
    intrinsics_dict: output of RealSenseCamera.get_intrinsics()
        (fx, fy, ppx, ppy, width, height, depth_scale)
    """
    depth_scale = intrinsics_dict.get("depth_scale") or 0.001  # RealSense default fallback

    depth_m = depth_raw_units.astype(np.float32) * float(depth_scale)
    depth_raw_u16 = depth_raw_units.astype(np.uint16)

    intr = Intrinsics(
        width=intrinsics_dict["width"],
        height=intrinsics_dict["height"],
        fx=intrinsics_dict["fx"],
        fy=intrinsics_dict["fy"],
        ppx=intrinsics_dict["ppx"],
        ppy=intrinsics_dict["ppy"],
        depth_scale=depth_scale,
    )

    return Frame(
        color=color_image,
        depth_raw=depth_raw_u16,
        depth_m=depth_m,
        intrinsics=intr,
        timestamp=time.time(),
    )


def _save_pointcloud(points, tx_id, output_dir):
    # [INSTRUMENT 3] Inside _save_pointcloud()
    log.info(f"[PERCEPTION_BRIDGE][3] output_dir={output_dir}")
    label = tx_id if tx_id else f"tx_{int(time.time() * 1000)}"
    filename = f"{label}.ply"
    filepath = os.path.join(output_dir, filename)
    log.info(f"[PERCEPTION_BRIDGE][3] filepath={filepath}")
    log.info(f"[PERCEPTION_BRIDGE][3] dir_exists={os.path.isdir(output_dir)}")
    log.info(f"[PERCEPTION_BRIDGE][3] point_count={None if points is None else points.shape[0]}")

    if points is None or points.shape[0] == 0:
        return None

    os.makedirs(output_dir, exist_ok=True)

    try:
        import open3d as o3d
        pcd = to_o3d(points)
        write_ok = o3d.io.write_point_cloud(filepath, pcd)
        log.info(f"[PERCEPTION_BRIDGE][3] write_point_cloud()={write_ok}")
        log.info(f"[PERCEPTION_BRIDGE][3] file_exists_after={os.path.isfile(filepath)}")
        return filepath

    except Exception as e:
        log.error(f"[PERCEPTION_BRIDGE] Failed to save point cloud: {e}")
        return None


def refine_measurement(
    color_image,
    depth_raw_units,
    intrinsics_dict,
    calibration_dir,
    tx_id=None,
    pointcloud_dir=DEFAULT_POINTCLOUD_DIR,
):

    print("\n========== ENTERED refine_measurement ==========")
    """
    Single-shot call: runs the perception_engine v7 pipeline on one
    captured RGB-D frame and persists the resulting point cloud to disk.
    Never raises -- on any failure returns success=False with a reason.
    """

    try:
        engine = _get_engine(calibration_dir)
    except FileNotFoundError as e:
        return {
            "success": False,
            "failure_reason": f"CALIBRATION_MISSING: {e}",
            "length_mm": 0.0, "width_mm": 0.0, "height_mm": 0.0,
            "volume_mm3": 0.0, "confidence": 0.0, "point_count": 0,
            "shape_type": "unknown", "pointcloud_path": None,
            "processing_time_ms": 0.0,
        }

    frame = _build_frame(color_image, depth_raw_units, intrinsics_dict)
    result = engine.measure(frame)

    print("\n========== AFTER engine.measure() ==========")
    print("result.success =", result.success)
    print("result.valid =", getattr(result, "valid", None))
    print("parcel_cloud is None =", result.parcel_cloud is None)
    if result.parcel_cloud is not None:
        print("parcel_cloud.shape =", result.parcel_cloud.shape)
        print("parcel_cloud.dtype =", result.parcel_cloud.dtype)


    # [INSTRUMENT 1] Immediately after measurement finishes
    log.info(f"[PERCEPTION_BRIDGE][1] result.success={result.success}")
    log.info(f"[PERCEPTION_BRIDGE][1] result.failure_reason={result.failure_reason}")
    log.info(f"[PERCEPTION_BRIDGE][1] result.parcel_cloud is None: {result.parcel_cloud is None}")
    log.info(f"[PERCEPTION_BRIDGE][1] type(result.parcel_cloud)={type(result.parcel_cloud)}")
    if result.parcel_cloud is not None:
        log.info(f"[PERCEPTION_BRIDGE][1] parcel_cloud.shape={result.parcel_cloud.shape}")
        log.info(f"[PERCEPTION_BRIDGE][1] parcel_cloud.dtype={result.parcel_cloud.dtype}")
        log.info(f"[PERCEPTION_BRIDGE][1] number of points={result.parcel_cloud.shape[0]}")

    pointcloud_path = None
    if result.success:
        # [INSTRUMENT 2] Immediately before _save_pointcloud()
        log.info("[PERCEPTION_BRIDGE][2] Calling _save_pointcloud()")
        pointcloud_path = _save_pointcloud(result.parcel_cloud, tx_id, pointcloud_dir)
        # [INSTRUMENT 4] Immediately after _save_pointcloud()
        log.info(f"[PERCEPTION_BRIDGE][4] _save_pointcloud() returned path={pointcloud_path}")

    # [INSTRUMENT 5] Immediately before inserting into the database
    log.info(f"[PERCEPTION_BRIDGE][5] pointcloud_url={pointcloud_path}")

    return {
        "success": result.success,
        "failure_reason": result.failure_reason,
        "length_mm": result.length_mm,
        "width_mm": result.width_mm,
        "height_mm": result.height_mm,
        "volume_mm3": result.volume_mm3,
        "confidence": result.confidence,
        "point_count": result.point_count,
        "shape_type": result.shape_type,
        "pointcloud_path": pointcloud_path,
        "processing_time_ms": result.processing_time_ms,
    }
