"""
measurement_engine.py

Top-level SDK entry point. Wires Stages 1-11 into a single call:

    engine = MeasurementEngine(calibration_dir="calibration")
    result = engine.measure(frame)

    print(result.length_mm, result.width_mm, result.height_mm, result.confidence)

Stage 2's background subtraction and the bed/scale plane (used by
Stage 9's height calc) both rely on calibration data, loaded once at
construction. Temporal filtering (part of Stage 3) is optional and only
useful in continuous/live-loop mode -- in single-shot mode there's no
frame history, so the temporal buffer step is skipped automatically when
only one frame has been seen (the noise filter still runs reject /
spatial-median / hole-fill on that single frame).
"""

from __future__ import annotations
import time
import numpy as np
import cv2

from .capture.frame import Frame
from .calibration.loader import CalibrationData, load_calibration
from .background.subtractor import BackgroundSubtractor
from .background.noise_filter import TemporalBuffer, NoiseSuppressor
from .segmentation.parcel_segmenter import segment_parcel
from .geometry.pointcloud import clean_point_cloud
from .measurement.obb import compute_oriented_bbox
from .measurement.estimator import estimate_height
from .measurement.confidence import compute_confidence
from .measurement.volume_estimator import compute_voxel_volume
from .core.measurement_result import MeasurementResult


class MeasurementEngine:
    def __init__(
        self,
        calibration_dir: str = "calibration",
        temporal_window: int = 5,
        use_temporal_filter: bool = True,
        min_height_m: float = 0.006,
    ):
        self.calibration: CalibrationData = load_calibration(calibration_dir)

        self.subtractor = BackgroundSubtractor(self.calibration.stable_empty_depth_m, min_height_m=min_height_m)
        self.noise_suppressor = NoiseSuppressor(self.calibration.noise_map_m)
        self.use_temporal_filter = use_temporal_filter
        self.temporal_buffer = TemporalBuffer(window_size=temporal_window) if use_temporal_filter else None

    # ------------------------------------------------------------------
    def measure(self, frame: Frame) -> MeasurementResult:
        t_start = time.perf_counter()

        try:
            depth_m = frame.depth_m

            # --- Stage 3: temporal median (if we have history), then
            # reject/spatial-median/hole-fill on the result ---
            if self.use_temporal_filter:
                depth_m = self.temporal_buffer.push(depth_m)
            depth_m = self.noise_suppressor.process(depth_m)

            # --- Stage 2: background subtraction + ROI crop ---
            # height_above_empty_m is the per-pixel "how far above the
            # calibrated empty scale surface is this point" map -- keep it
            # (previously discarded) so segment_parcel can do a strict
            # per-point height re-check, not just trust the binary mask.
            foreground_mask, height_above_empty_m = self.subtractor.subtract(depth_m)

            # Zero out everything outside the calibrated ROI so stray
            # objects (hands, background clutter) can't enter segmentation.
            x1, y1, x2, y2 = self.calibration.roi
            roi_mask = np.zeros_like(foreground_mask)
            roi_mask[y1:y2, x1:x2] = 255
            foreground_mask = cv2.bitwise_and(foreground_mask, roi_mask)

            if foreground_mask.sum() == 0:
                return MeasurementResult.failure(
                    "No foreground detected (empty scene or object outside ROI).",
                    (time.perf_counter() - t_start) * 1000,
                )

            # --- Stage 5 (incl. Stage 4 plane removal internally) ---
            seg = segment_parcel(
                depth_m, foreground_mask, frame.intrinsics,
                scale_center=self.calibration.scale_center,
                height_above_empty_m=height_above_empty_m,
                min_height_m=self.subtractor.min_height_m,
            )
            if seg is None:
                return MeasurementResult.failure(
                    "Could not isolate a parcel cluster from the foreground.",
                    (time.perf_counter() - t_start) * 1000,
                )

            if seg.multiple_parcels:
                return MeasurementResult.failure(
                    "Multiple parcels detected on platform. Remove all but one "
                    "parcel and retry -- one parcel per transaction.",
                    (time.perf_counter() - t_start) * 1000,
                )

            # --- Stage 6: point cloud cleaning ---
            cleaned_points, kept_idx = clean_point_cloud(seg.points)
            if cleaned_points.shape[0] < 12:
                return MeasurementResult.failure(
                    "Too few points survived outlier removal.",
                    (time.perf_counter() - t_start) * 1000,
                )
            cleaned_pixels = seg.pixel_coords[kept_idx]

            # --- Stage 9: height (plane-to-plane, using calibration) ---
            height_result = estimate_height(
                cleaned_points,
                scale_height_mm=self.calibration.scale_height_mm,
                scale_depth_mm=self.calibration.scale_depth_mm,
            )

            # --- Stage 7/8: OBB + PCA/hull corner refinement ---
            # For a flat-top box, the RANSAC top-plane normal (just
            # computed in Stage 9) is reliable and lets the footprint
            # rect align exactly with the parcel's real top face. For an
            # irregular/curved/lumpy top, that same RANSAC fit was only
            # describing an arbitrary sub-patch -- using its normal here
            # would tilt the footprint projection randomly. Fall back to
            # a stable camera-facing normal (consistent with the
            # overhead-mounted D455) so L/W still come out sane for any
            # footprint shape, regardless of the top surface's geometry.
            # The footprint's ORIENTATION should always follow the real
            # fitted plane when one exists -- RANSAC correctly found the
            # base object's actual top surface (it has the most inlier
            # points precisely because it's the bigger, flatter surface).
            # used_secondary_top only means a SMALLER stacked item is
            # taller -- that's a height correction, not evidence the
            # plane's orientation is wrong. Forcing a fixed camera-facing
            # normal here (instead of the real fitted plane) throws away
            # a correct rotation in exchange for an approximation that
            # drifts from the parcel's true orientation whenever the
            # camera isn't mounted perfectly perpendicular to the table
            # -- which is almost always, by some small amount. Only fall
            # back to the fixed normal for genuinely non-planar tops,
            # where no real plane exists to take an orientation from.
            if height_result.is_planar_top:
                obb_normal = height_result.top_plane.normal
            else:
                obb_normal = np.array([0.0, 0.0, -1.0])

            obb_result = compute_oriented_bbox(
                cleaned_points,
                plane_normal=obb_normal,
            )

            # Trim factors calibrated against a known 400x300x200mm test box.
            # Hull/minAreaRect slightly overestimates footprint (~4%);
            # height plane-fitting slightly overestimates (~5%).
            # Adjust TRIM_LW / TRIM_H if your setup differs.
            TRIM_LW = 0.962
            TRIM_H  = 0.950
            length_mm = obb_result.length_m * 1000.0 * TRIM_LW
            width_mm  = obb_result.width_m  * 1000.0 * TRIM_LW
            height_mm = height_result.height_m * 1000.0 * TRIM_H

            # ---------------------------------------------------------------
            # Plausibility gate: if height is absurdly large relative to L/W
            # (e.g. H=253mm from a 22×8mm footprint — a specular artifact),
            # treat it as a measurement failure rather than returning garbage.
            # Real parcels have height ≤ ~3× their shortest dimension.
            # ---------------------------------------------------------------
            max_lw_mm = max(length_mm, width_mm)
            if max_lw_mm > 0 and height_mm > max_lw_mm * 5.0 and cleaned_points.shape[0] < 100:
                return MeasurementResult.failure(
                    f"Implausible dimensions (H={height_mm:.0f}mm >> L={length_mm:.0f}×W={width_mm:.0f}mm "
                    f"from only {cleaned_points.shape[0]} pts) — likely specular/glare artifact.",
                    (time.perf_counter() - t_start) * 1000,
                )

            # ---------------------------------------------------------------
            # Volume: use voxel column integration for real irregular volume.
            # For a flat box this matches L×W×H closely; for irregular objects
            # (poly bags, rounded parcels) it gives actual occupied volume.
            # Falls back to bounding-box volume if point cloud is too sparse.
            # ---------------------------------------------------------------
            scale_depth_m = self.calibration.scale_depth_mm / 1000.0
            voxel_volume_m3, voxel_footprint_m2 = compute_voxel_volume(
                cleaned_points, scale_depth_m=scale_depth_m,
            )
            used_voxel_volume = voxel_volume_m3 > 0
            if used_voxel_volume:
                volume_mm3 = voxel_volume_m3 * 1e9   # m³ → mm³
            else:
                # Fallback for very sparse clouds (cloud spans < 1 voxel cell
                # in X or Y -- compute_voxel_volume bails out rather than
                # guess). Bounding-box volume is the best available estimate.
                volume_mm3 = length_mm * width_mm * height_mm

            # Scale the actual measured rect corners by the same trim factor,
            # around their own centroid, so the visualized box exactly matches
            # the L/W numbers above (instead of drawing an untrimmed,
            # independently-fit Open3D box).
            rect_center = obb_result.rect_corners_3d.mean(axis=0, keepdims=True)
            rect_corners_3d_trimmed = (
                rect_center + (obb_result.rect_corners_3d - rect_center) * TRIM_LW
            ).astype(np.float32)

            # --- Stage 10: confidence ---
            confidence = compute_confidence(
                parcel_points=cleaned_points,
                top_plane=height_result.top_plane,
                top_plane_inlier_ratio=height_result.top_plane_inlier_ratio,
                mask=seg.mask,
                hull_points_2d=obb_result.hull_points_2d,
                rect_length_m=obb_result.length_m,
                rect_width_m=obb_result.width_m,
                noise_map_m=self.calibration.noise_map_m,
                pixel_coords=cleaned_pixels,
                is_planar_top=height_result.is_planar_top,
            )

            elapsed_ms = (time.perf_counter() - t_start) * 1000.0

            hull_area_m2 = cv2.contourArea(obb_result.hull_points_2d.astype(np.float32)) \
                if obb_result.hull_points_2d.shape[0] >= 3 else 0.0
            # Report the SAME footprint the volume figure was integrated
            # over (voxel footprint) when voxel volume was actually used,
            # so the on-screen "footprint cm²" and "Vol L" numbers are
            # always mutually consistent. Hull area remains the fallback.
            footprint_area_mm2 = (voxel_footprint_m2 if used_voxel_volume else hull_area_m2) * 1e6
            shape_type = "rectangular" if confidence.shape_regularity >= 0.85 else "irregular"

            return MeasurementResult(
                length_mm=length_mm,
                width_mm=width_mm,
                height_mm=height_mm,
                volume_mm3=volume_mm3,
                confidence=confidence.overall,
                point_count=cleaned_points.shape[0],
                processing_time_ms=elapsed_ms,
                footprint_area_mm2=footprint_area_mm2,
                shape_type=shape_type,
                is_planar_top=height_result.is_planar_top,
                used_secondary_top=height_result.used_secondary_top,
                obb=obb_result.obb_o3d,
                rect_corners_3d=rect_corners_3d_trimmed,
                parcel_cloud=cleaned_points,
                mask=seg.mask,
                success=True,
                confidence_breakdown=confidence.as_dict(),
            )

        except Exception as exc:
            return MeasurementResult.failure(
                f"Unexpected error: {exc!r}",
                (time.perf_counter() - t_start) * 1000,
            )
