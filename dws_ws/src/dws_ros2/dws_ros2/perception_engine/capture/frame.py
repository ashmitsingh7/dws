"""
capture/frame.py

Stage 1 — plain data containers for a single captured frame.
No RealSense-specific types leak past this module; everything downstream
works with these plain numpy/dataclass types so the rest of the engine
is hardware-agnostic and unit-testable without a camera.
"""

from __future__ import annotations
from dataclasses import dataclass
import numpy as np


@dataclass
class Intrinsics:
    """Pinhole camera intrinsics, in pixels, for the depth (aligned) stream."""
    width: int
    height: int
    fx: float
    fy: float
    ppx: float  # principal point x
    ppy: float  # principal point y
    depth_scale: float  # multiply raw uint16 depth by this to get meters

    def deproject(self, u: np.ndarray, v: np.ndarray, depth_m: np.ndarray) -> np.ndarray:
        """
        Vectorized back-projection of pixel coords + depth (meters) to
        camera-space 3D points (meters). u, v, depth_m are 1D arrays of
        equal length. Returns (N, 3) float32 array.
        """
        x = (u - self.ppx) * depth_m / self.fx
        y = (v - self.ppy) * depth_m / self.fy
        z = depth_m
        return np.stack([x, y, z], axis=-1).astype(np.float32)


@dataclass
class Frame:
    """A single aligned RGB-D capture."""
    color: np.ndarray       # (H, W, 3) uint8, BGR
    depth_raw: np.ndarray   # (H, W) uint16, raw depth units (not yet scaled)
    depth_m: np.ndarray     # (H, W) float32, depth in meters (0 = invalid)
    intrinsics: Intrinsics
    timestamp: float        # seconds, monotonic

    @property
    def shape(self):
        return self.depth_m.shape
