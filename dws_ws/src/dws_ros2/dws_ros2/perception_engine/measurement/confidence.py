"""
measurement/confidence.py

Stage 10 — Confidence Score (Cognex-style multi-factor scoring).

Combines several independent quality signals into a single 0-100 score:
  - plane fit quality (top-face inlier ratio)
  - noise (relative spread of points around the fitted top plane)
  - missing-data percentage (holes in the parcel mask after segmentation)
  - point cloud coverage (point density vs expected for the bbox area)
  - cluster density (how tightly the DBSCAN cluster held together)
  - OBB fit (how well the convex hull area matches the minAreaRect area
    -- a poor fit means a non-rectangular or occluded object)
  - residual error (RMS distance of points to the fitted top plane)

Each factor maps to [0, 1] and the final score is a weighted average,
scaled to a percentage. Weights are tunable; defaults favor plane fit
and residual error since those most directly affect L/W/H accuracy.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
import cv2


@dataclass
class ConfidenceBreakdown:
    plane_fit: float
    noise: float
    missing_data: float
    coverage: float
    cluster_density: float
    obb_fit: float
    residual_error: float
    shape_regularity: float  # informational only, NOT weighted into overall --
                              # how rectangular the footprint is (1.0 = perfect
                              # rect, lower = round/irregular/odd shape). A low
                              # value here is a description of the parcel, not
                              # a measurement defect, so it isn't penalized.
    overall: float

    def as_dict(self) -> dict:
        return {
            "plane_fit": round(self.plane_fit, 3),
            "noise": round(self.noise, 3),
            "missing_data": round(self.missing_data, 3),
            "coverage": round(self.coverage, 3),
            "cluster_density": round(self.cluster_density, 3),
            "obb_fit": round(self.obb_fit, 3),
            "residual_error": round(self.residual_error, 3),
            "shape_regularity": round(self.shape_regularity, 3),
            "overall": round(self.overall, 1),
        }


_WEIGHTS = {
    "plane_fit": 0.20,
    "noise": 0.10,
    "missing_data": 0.10,
    "coverage": 0.15,
    "cluster_density": 0.15,
    "obb_fit": 0.05,
    "residual_error": 0.20,
}
# NOTE: obb_fit's weight is intentionally small. It used to be the same
# formula as cluster_density (hull_area / rect_area, i.e. "how
# rectangular is this") counted TWICE -- once at 0.15 and once at 0.10 --
# which meant any genuinely round/odd-shaped parcel got penalized twice
# for a shape property, not a measurement defect. cluster_density below
# is now a real point-density signal instead, and the rectangularity
# ratio is reported once, at low weight, as a minor sanity check rather
# than a primary score driver.


def compute_confidence(
    parcel_points: np.ndarray,
    top_plane,
    top_plane_inlier_ratio: float,
    mask: np.ndarray,
    hull_points_2d: np.ndarray,
    rect_length_m: float,
    rect_width_m: float,
    noise_map_m: np.ndarray,
    pixel_coords: np.ndarray,
    is_planar_top: bool = True,
) -> ConfidenceBreakdown:
    n = parcel_points.shape[0]

    # --- plane fit: how cleanly a flat top face separates from noise.
    # Only meaningful for a genuinely flat-top parcel. For an irregular
    # top (poly bag, lumpy item), estimator.py intentionally did NOT rely
    # on this plane fit for height -- so a low inlier ratio here reflects
    # the parcel's real shape, not a defect, and shouldn't be penalized
    # as if the measurement is unreliable. Substitute a neutral score
    # (a flat-top weighting would unfairly tank confidence for items that
    # were never expected to have one). ---
    if is_planar_top:
        plane_fit = float(np.clip(top_plane_inlier_ratio, 0.0, 1.0))
    else:
        plane_fit = 0.75  # neutral -- "not applicable", not "bad"

    # --- noise: calibrated sensor noise at the parcel's pixels, normalized ---
    if pixel_coords.shape[0] > 0:
        local_noise = noise_map_m[pixel_coords[:, 0], pixel_coords[:, 1]]
        mean_noise_mm = float(np.mean(local_noise) * 1000.0)
    else:
        mean_noise_mm = 5.0
    # 0mm noise -> score 1.0; 5mm+ calibrated noise -> score floors at ~0.2
    noise_score = float(np.clip(1.0 - mean_noise_mm / 6.0, 0.0, 1.0))

    # --- missing data: holes inside the mask's own bounding region ---
    if mask.sum() > 0:
        ys, xs = np.nonzero(mask)
        y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
        bbox_area = (y1 - y0 + 1) * (x1 - x0 + 1)
        filled_area = mask[y0:y1 + 1, x0:x1 + 1].astype(bool).sum()
        missing_frac = 1.0 - (filled_area / max(bbox_area, 1))
    else:
        missing_frac = 1.0
    missing_data_score = float(np.clip(1.0 - missing_frac * 2.0, 0.0, 1.0))

    # --- coverage: point count vs a reasonable expectation for this size ---
    # Rough heuristic: at D455 working range, a well-segmented parcel
    # face should yield on the order of a few thousand points minimum.
    # Floor lowered (was effectively gated by a hard >=30 point minimum
    # upstream) so small/thin/sparse irregular parcels get a graded
    # score instead of being treated identically to a near-failure case.
    coverage_score = float(np.clip(n / 3000.0, 0.0, 1.0))

    hull_area = cv2.contourArea(hull_points_2d.astype(np.float32)) if hull_points_2d.shape[0] >= 3 else 0.0
    rect_area = rect_length_m * rect_width_m

    # --- shape regularity: hull area vs rect area -- 1.0 for a perfect
    # rectangle, lower for round/triangular/odd footprints. Reported as a
    # descriptive diagnostic ONLY -- not weighted into `overall` -- so an
    # irregular but correctly-measured parcel isn't scored as "bad". ---
    shape_regularity = float(np.clip(hull_area / rect_area, 0.0, 1.0)) if rect_area > 1e-9 else 0.0

    # --- obb_fit: kept as a small-weight sanity check (very low values
    # can indicate the hull collapsed to near-zero area, a real defect,
    # not just "round") ---
    obb_fit_score = shape_regularity

    # --- cluster density: points per unit footprint area vs a reasonable
    # expectation for the D455's resolution at typical working distance.
    # This is a genuine, distinct quality signal from shape_regularity --
    # a sparse, noisy capture scores low here regardless of whether the
    # footprint is a rectangle or a circle. ---
    if hull_area > 1e-9:
        points_per_mm2 = n / (hull_area * 1e6)
        # ~0.1 points/mm^2 (100 pts/cm^2) is a dense, well-resolved
        # capture; sparser irregular/thin parcels grade down smoothly.
        cluster_density_score = float(np.clip(points_per_mm2 / 0.10, 0.0, 1.0))
    else:
        cluster_density_score = 0.0

    # --- residual error: RMS distance of points to the fitted top plane.
    # Same caveat as plane_fit -- only a real quality signal when the top
    # genuinely is planar. ---
    if is_planar_top:
        dists = top_plane.signed_distance(parcel_points)
        rms_m = float(np.sqrt(np.mean(dists ** 2)))
        # 0mm residual -> 1.0; 8mm+ RMS -> floors near 0
        residual_score = float(np.clip(1.0 - (rms_m * 1000.0) / 8.0, 0.0, 1.0))
    else:
        residual_score = 0.75  # neutral -- not applicable for a non-planar top

    overall = (
        _WEIGHTS["plane_fit"] * plane_fit
        + _WEIGHTS["noise"] * noise_score
        + _WEIGHTS["missing_data"] * missing_data_score
        + _WEIGHTS["coverage"] * coverage_score
        + _WEIGHTS["cluster_density"] * cluster_density_score
        + _WEIGHTS["obb_fit"] * obb_fit_score
        + _WEIGHTS["residual_error"] * residual_score
    ) * 100.0

    # -----------------------------------------------------------------------
    # HARD FLOORS — override the weighted average when something is physically
    # implausible, regardless of how well the small cluster fits itself.
    # These prevent glare artifacts / noise specks from faking high confidence.
    # -----------------------------------------------------------------------

    # 1) Too few points: a cluster under 50 points cannot reliably measure
    #    anything. A true thin envelope on a flat scale returns 200-500 pts;
    #    14 pts is sensor noise or a specular glare dot.
    if n < 50:
        overall = min(overall, 20.0)

    # 2) Physically absurd aspect ratio: height > 5× the max(L, W) from a
    #    tiny footprint is a specular artifact signature (we saw H=253mm from
    #    a 22×8mm footprint — physically impossible for any real parcel).
    if rect_length_m > 0 and rect_width_m > 0:
        max_lw_mm = max(rect_length_m, rect_width_m) * 1000.0
        # We don't have height_mm here, but we can detect the degenerate
        # hull: if the hull area is < 1 cm² AND the cluster is tiny → cap.
        if hull_area * 1e6 < 100.0 and n < 100:  # hull < 1 cm² and sparse
            overall = min(overall, 15.0)

    return ConfidenceBreakdown(
        plane_fit=plane_fit,
        noise=noise_score,
        missing_data=missing_data_score,
        coverage=coverage_score,
        cluster_density=cluster_density_score,
        obb_fit=obb_fit_score,
        residual_error=residual_score,
        shape_regularity=shape_regularity,
        overall=float(np.clip(overall, 0.0, 100.0)),
    )
