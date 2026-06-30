"""
measurement/estimator.py

Stage 9 — Height Estimation.

Instead of max(z) - min(z) on the raw cloud (noisy, sensitive to single
outlier points), compute height as the perpendicular distance between
the parcel's top-face plane and the calibrated scale/bed plane. This is
far more stable because both planes are RANSAC-fit (averaging over many
points) rather than relying on single extremal points.
"""

from __future__ import annotations
from dataclasses import dataclass
import numpy as np

from ..geometry.plane import Plane, segment_plane


@dataclass
class HeightResult:
    height_m: float
    top_plane: Plane
    top_plane_inlier_ratio: float
    is_planar_top: bool  # False when the percentile fallback was used
                          # (irregular/curved/lumpy top, not a flat box face)
    used_secondary_top: bool = False  # True when the percentile cross-check
                          # found a real, supported surface HIGHER than the
                          # dominant RANSAC plane (e.g. a smaller parcel
                          # stacked on a bigger one) and that height was
                          # used instead of blindly trusting whichever
                          # plane RANSAC happened to lock onto.


def estimate_height(
    parcel_points: np.ndarray,
    scale_height_mm: float,
    scale_depth_mm: float,
    camera_origin: np.ndarray | None = None,
    plane_distance_threshold: float = 0.004,
    planar_inlier_threshold: float = 0.6,
    min_secondary_support_points: int = 15,
    secondary_top_tolerance_m: float = 0.006,
    secondary_top_margin_m: float = 0.003,
) -> HeightResult:
    """
    parcel_points: (N, 3) meters -- cleaned parcel cloud (Stage 6 output).
    scale_height_mm: from calibration geometry -- height of the scale
        surface above the floor (i.e. floor_depth_mm - scale_depth_mm).
    scale_depth_mm: from calibration geometry -- the scale surface's
        depth (distance from camera) when empty.

    Returns parcel height in meters: distance from the scale surface to
    the parcel's true highest surface.

    Why a single RANSAC plane fit is not enough on its own: when a
    smaller object sits on top of a bigger one (e.g. a small parcel
    placed on a larger box), there are genuinely TWO comparably-sized
    flat surfaces in the cloud -- the big box's top and the small
    parcel's top. RANSAC's stochastic sampling can lock onto either one
    frame-to-frame even with near-identical point counts, which makes
    the reported height jump around (e.g. 201 -> 259 -> 313mm across
    repeated captures of the exact same physical stack) even though
    nothing about the scene changed. Tightening RANSAC parameters can't
    fix this -- it's not noise, it's ambiguity between two real planes.

    The fix: ALWAYS compute a percentile-based "true top" height as an
    independent cross-check (the 5th percentile of "closeness to
    camera" across every parcel point -- not the bare min, to stay
    robust to a single noisy near-outlier pixel), regardless of whether
    RANSAC found a dominant plane. If that percentile height is both
    (a) meaningfully taller than the dominant-plane height, and (b)
    backed by real point support near that height (not one stray
    pixel), trust it instead -- the dominant plane was real, but it
    wasn't the top. This is also exactly the right behavior for a
    genuinely irregular/curved top (poly bag, pouch) with no plane at
    all, where the percentile estimate is the only thing available.
    """
    if camera_origin is None:
        camera_origin = np.array([0.0, 0.0, 0.0])

    top_plane = segment_plane(
        parcel_points,
        distance_threshold=plane_distance_threshold,
        orient_towards=camera_origin,
    )
    top_inlier_ratio = len(top_plane.inlier_indices) / parcel_points.shape[0]
    is_planar_top = top_inlier_ratio >= planar_inlier_threshold

    scale_depth_m = scale_depth_mm / 1000.0

    # --- Always compute the percentile-based true-top height, even when
    # a dominant plane was found. Independent of which plane RANSAC fit. ---
    closest_depth_m = float(np.percentile(parcel_points[:, 2], 5))
    percentile_height_m = scale_depth_m - closest_depth_m

    used_secondary_top = False

    if is_planar_top:
        # Distance from camera origin to the top plane, ALONG THE OPTICAL
        # AXIS (z), to match the convention auto_calibration_v2.py used
        # for scale_depth_mm/floor_depth_mm (raw depth-camera z values,
        # not full 3D Euclidean distances). Solved at the plane's
        # centroid (x, y) rather than assuming exact perpendicularity.
        inlier_pts = parcel_points[top_plane.inlier_indices]
        cx, cy = inlier_pts[:, 0].mean(), inlier_pts[:, 1].mean()

        a, b, c, d = top_plane.a, top_plane.b, top_plane.c, top_plane.d
        if abs(c) < 1e-6:
            top_plane_depth_m = float(inlier_pts[:, 2].mean())
        else:
            top_plane_depth_m = -(a * cx + b * cy + d) / c

        plane_height_m = scale_depth_m - top_plane_depth_m

        # How many points genuinely sit near the percentile-derived top
        # (within a small tolerance), i.e. is this a real supported
        # surface and not just one noisy pixel?
        support = int(np.count_nonzero(parcel_points[:, 2] <= closest_depth_m + secondary_top_tolerance_m))

        if (percentile_height_m > plane_height_m + secondary_top_margin_m
                and support >= min_secondary_support_points):
            height_m = percentile_height_m
            used_secondary_top = True
        else:
            height_m = plane_height_m
    else:
        # Irregular/non-planar top: no reliable single plane exists at
        # all -- the percentile estimate IS the measurement.
        height_m = percentile_height_m

    return HeightResult(
        height_m=max(0.0, float(height_m)),
        top_plane=top_plane,
        top_plane_inlier_ratio=float(top_inlier_ratio),
        is_planar_top=is_planar_top,
        used_secondary_top=used_secondary_top,
    )
