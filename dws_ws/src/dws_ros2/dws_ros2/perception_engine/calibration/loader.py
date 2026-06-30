"""
calibration/loader.py

Consumes the three files produced by auto_calibration_v2.py:
    - stable_empty_depth.npy   (H, W) float32, mm, median empty-scene depth
    - noise_map.npy            (H, W) float32, mm, per-pixel std-dev
    - system_geometry.npy      dict (via np.save with allow_pickle), keys:
        floor_depth_mm, scale_depth_mm, scale_height_mm,
        scale_center_x, scale_center_y,
        roi_x1, roi_y1, roi_x2, roi_y2

Note: auto_calibration_v2.py stores depth in millimeters (it computes
PLANE_TOLERANCE_MM, floor_depth_mm etc. directly off the raw uint16 depth
stream without dividing by depth_scale). This loader keeps mm as the
native unit for calibration data and is responsible for converting to
meters where the rest of the engine (which works in meters, matching
RealSense's native unit) needs it.
"""

from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Optional
import numpy as np


@dataclass
class CalibrationData:
    stable_empty_depth_mm: np.ndarray  # (H, W) float32
    noise_map_mm: np.ndarray           # (H, W) float32
    geometry: dict

    @property
    def stable_empty_depth_m(self) -> np.ndarray:
        return self.stable_empty_depth_mm / 1000.0

    @property
    def noise_map_m(self) -> np.ndarray:
        return self.noise_map_mm / 1000.0

    @property
    def roi(self) -> tuple[int, int, int, int]:
        g = self.geometry
        return int(g["roi_x1"]), int(g["roi_y1"]), int(g["roi_x2"]), int(g["roi_y2"])

    @property
    def scale_height_mm(self) -> float:
        return float(self.geometry["scale_height_mm"])

    @property
    def scale_depth_mm(self) -> float:
        return float(self.geometry["scale_depth_mm"])

    @property
    def floor_depth_mm(self) -> float:
        return float(self.geometry["floor_depth_mm"])

    @property
    def scale_center(self) -> tuple[int, int]:
        """(x, y) pixel coords of the scale centre, from calibration."""
        return (int(self.geometry["scale_center_x"]), int(self.geometry["scale_center_y"]))


def _load_geometry(path: str) -> dict:
    """
    system_geometry.npy was saved via np.save(path, geometry_dict), which
    wraps the dict in a 0-d object array. Handle that, plus a plain dict
    saved with allow_pickle, plus (defensively) a JSON-serialized fallback
    in case a future calibration version switches formats.
    """
    raw = np.load(path, allow_pickle=True)

    if isinstance(raw, np.ndarray):
        if raw.shape == ():
            item = raw.item()
            if isinstance(item, dict):
                return item
        if raw.dtype == object and raw.size == 1:
            item = raw.reshape(-1)[0]
            if isinstance(item, dict):
                return item

    if isinstance(raw, dict):
        return raw

    raise ValueError(
        f"Could not interpret {path} as a geometry dict (got {type(raw)}, "
        f"shape {getattr(raw, 'shape', None)})"
    )


def load_calibration(calibration_dir: str = "calibration") -> CalibrationData:
    """
    Load all three calibration artifacts from `calibration_dir`. Raises
    FileNotFoundError with a clear message if any are missing — fail
    loudly rather than silently degrading measurement quality.
    """
    paths = {
        "stable_empty_depth": os.path.join(calibration_dir, "stable_empty_depth.npy"),
        "noise_map": os.path.join(calibration_dir, "noise_map.npy"),
        "system_geometry": os.path.join(calibration_dir, "system_geometry.npy"),
    }

    missing = [name for name, p in paths.items() if not os.path.exists(p)]
    if missing:
        raise FileNotFoundError(
            f"Missing calibration files in '{calibration_dir}': {missing}. "
            f"Run auto_calibration_v2.py first."
        )

    stable_empty_depth = np.load(paths["stable_empty_depth"]).astype(np.float32)
    noise_map = np.load(paths["noise_map"]).astype(np.float32)
    geometry = _load_geometry(paths["system_geometry"])

    return CalibrationData(
        stable_empty_depth_mm=stable_empty_depth,
        noise_map_mm=noise_map,
        geometry=geometry,
    )
