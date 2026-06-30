"""
core/measurement_result.py

Stage 11 — Industrial Output Contract.

This is the single object every consumer of the engine (UI, MES, logger,
billing system) should depend on. Mirrors the shape of commercial parcel
dimensioning SDK outputs (Cognex / vMeasure style).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import numpy as np


@dataclass
class MeasurementResult:
    # --- Primary outputs ---
    length_mm: float = 0.0
    width_mm: float = 0.0
    height_mm: float = 0.0
    volume_mm3: float = 0.0

    # --- Quality / diagnostics ---
    confidence: float = 0.0  # 0-100
    point_count: int = 0
    processing_time_ms: float = 0.0
    footprint_area_mm2: float = 0.0  # actual hull area (not L*W bbox area) --
                                      # meaningful for irregular footprints
    shape_type: str = "unknown"  # "rectangular" or "irregular", from
                                  # shape_regularity (hull/rect area ratio)
    is_planar_top: bool = True   # False if height used the percentile
                                  # fallback for a non-flat top
    used_secondary_top: bool = False  # True if a smaller object stacked
                                  # on a bigger one was detected and the
                                  # taller (correct) surface was used for
                                  # height instead of the dominant plane

    # --- Geometry payload (for visualization / downstream use) ---
    obb: Optional[object] = None  # open3d.geometry.OrientedBoundingBox (Open3D's own
                                   # independent fit -- kept for 3D sanity-checking only,
                                   # NOT guaranteed to match length_mm/width_mm exactly)
    rect_corners_3d: Optional[np.ndarray] = None  # (4, 3) the EXACT measured rectangle
                                   # (trim factor applied), top-face only -- use this
                                   # for any visualization that should match L/W/H.
    parcel_cloud: Optional[np.ndarray] = None  # (N, 3) float32, in camera/world frame
    mask: Optional[np.ndarray] = None  # (H, W) uint8, 255 = parcel

    # --- Status ---
    success: bool = False
    failure_reason: Optional[str] = None

    # --- Confidence breakdown (Stage 10), kept for debugging/logging ---
    confidence_breakdown: dict = field(default_factory=dict)

    def summary(self) -> str:
        if not self.success:
            return f"MEASUREMENT FAILED: {self.failure_reason}"
        vol_label = "Vol(voxel)" if self.shape_type == "irregular" else "Vol(bbox)"
        return (
            f"L={self.length_mm:.1f}mm  W={self.width_mm:.1f}mm  "
            f"H={self.height_mm:.1f}mm  {vol_label}={self.volume_mm3/1e6:.3f}L  "
            f"Shape={self.shape_type}  Confidence={self.confidence:.1f}%  "
            f"({self.processing_time_ms:.0f} ms, {self.point_count} pts)"
        )

    @staticmethod
    def failure(reason: str, processing_time_ms: float = 0.0) -> "MeasurementResult":
        return MeasurementResult(
            success=False,
            failure_reason=reason,
            processing_time_ms=processing_time_ms,
        )
