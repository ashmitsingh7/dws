"""
segmentation/parcel_segmenter.py

Stage 5 — Parcel Segmentation (hybrid pipeline).

Foreground Mask -> Plane Removal -> Connected Components -> DBSCAN
(on a voxel-downsampled cloud) -> Largest Cluster -> map back to
full-resolution points.

DBSCAN is run on a voxel-downsampled point cloud, not the raw
deprojected cloud. Running DBSCAN's radius-neighbor search directly on
~100k raw points packed densely into a small volume degrades towards
an all-pairs search and is the single biggest cost in this pipeline.
Downsampling first, clustering, then recovering full-resolution
membership via the voxel index is both correct and fast.
"""

from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import cv2
from sklearn.cluster import DBSCAN

from ..capture.frame import Intrinsics
from ..geometry.pointcloud import deproject_masked, voxel_downsample_with_index
from ..geometry.plane import segment_plane, Plane


@dataclass
class SegmentationResult:
    points: np.ndarray          # (N, 3) float32, meters -- full-res parcel points
    pixel_coords: np.ndarray    # (N, 2) int32 -- (row, col) per point
    mask: np.ndarray            # (H, W) uint8 -- final parcel mask, 255=parcel
    bed_plane: Plane            # the dominant (table/scale) plane removed
    plane_inlier_ratio: float   # fraction of foreground points that were "the plane"
    multiple_parcels: bool = False  # True if >1 significant DBSCAN cluster found


def _largest_connected_component(mask: np.ndarray) -> np.ndarray:
    """Keep only the largest connected component of a binary mask."""
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if num_labels <= 1:
        return mask  # nothing but background
    # label 0 is background; find largest among 1..num_labels-1 by area
    areas = stats[1:, cv2.CC_STAT_AREA]
    largest_label = 1 + int(np.argmax(areas))
    out = np.where(labels == largest_label, 255, 0).astype(np.uint8)
    return out


def _whole_candidate_as_result(
    candidate_points: np.ndarray,
    candidate_pixels: np.ndarray,
    h: int,
    w: int,
    plane: Plane,
    plane_inlier_ratio: float,
) -> SegmentationResult:
    """
    Fallback: treat the entire (ROI-clipped, largest-connected-component)
    foreground blob as the parcel directly, skipping DBSCAN entirely.

    DBSCAN assumes a reasonably dense, uniform point cloud -- a fair
    assumption for a solid box's top face, but it breaks down for thin,
    sparse, or low-reflectivity irregular parcels (flat envelopes, glossy
    poly bags) where the IR depth sensor returns few/sparse valid points.
    In that case the 2D foreground mask + ROI + scale-centre gating
    already isolated a single connected blob -- there's no need to
    re-discover that via density clustering, and doing so just produces
    false rejections.
    """
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[candidate_pixels[:, 0], candidate_pixels[:, 1]] = 255
    return SegmentationResult(
        points=candidate_points,
        pixel_coords=candidate_pixels,
        mask=mask,
        bed_plane=plane,
        plane_inlier_ratio=plane_inlier_ratio,
    )


def segment_parcel(
    depth_m: np.ndarray,
    foreground_mask: np.ndarray,
    intrinsics: Intrinsics,
    scale_center: tuple[int, int] | None = None,
    height_above_empty_m: np.ndarray | None = None,
    min_height_m: float = 0.006,
    plane_distance_threshold: float = 0.004,
    dbscan_eps: float = 0.010,
    dbscan_min_samples: int = 10,
    voxel_size: float = 0.004,
    min_cluster_fraction: float = 0.05,
    min_cluster_points: int = 12,
    edge_erosion_px: int = 2,
    min_cluster_relative_to_largest: float = 0.15,
) -> SegmentationResult | None:
    """
    Run the full Stage 5 hybrid pipeline. Returns None only if there's
    truly no plausible parcel (empty scene). For irregular parcels where
    DBSCAN's density assumption doesn't hold (thin/sparse/odd-shaped
    objects), falls back to the whole ROI-gated foreground blob rather
    than rejecting outright -- see _whole_candidate_as_result.

    height_above_empty_m / min_height_m: the same per-pixel "distance
    above the calibrated empty scale surface" map and threshold used by
    BackgroundSubtractor. The 2D binary foreground mask already applied
    this threshold once -- but morphological close/open afterward can
    bridge a thin gap between the parcel and a marginally-foreground
    strip of the scale plate itself (e.g. if the calibrated empty-surface
    baseline is slightly stale or borrowed from a different session, or
    a metal grated platform gives uneven IR return). Re-checking each
    *candidate point* directly against its exact calibrated height, with
    a stricter margin than the mask used, is a second, independent line
    of defense against weighing-scale contamination leaking into the
    parcel cluster.
    """
    h, w = depth_m.shape

    # --- Connected components on the 2D foreground mask first ---
    # Cheap pre-filter: drop small speckle blobs before even deprojecting.
    cleaned_mask = _largest_connected_component(foreground_mask)
    if cleaned_mask.sum() == 0:
        return None

    # --- Strip flying-pixel / mixed-depth edge artifacts ---
    # Stereo depth sensors (RealSense included) produce a thin band of
    # invalid "flying pixel" depth values right at an object's silhouette
    # edge, where the depth estimator blends the object's true depth with
    # the background/platform depth behind it. Deprojected, this shows up
    # as a sparse ring of points at intermediate depth, spatially offset
    # from both the object's top face and the platform -- dense enough in
    # places to independently clear DBSCAN's cluster-significance bar and
    # get counted as a second "parcel" below, even though it is sensor
    # noise. Erode the mask by a couple of pixels before deprojecting to
    # remove this band. If erosion would wipe out a thin/small parcel
    # entirely, fall back to the un-eroded mask rather than losing the
    # candidate.
    if edge_erosion_px > 0:
        kernel = np.ones(
            (edge_erosion_px * 2 + 1, edge_erosion_px * 2 + 1), np.uint8
        )
        eroded_mask = cv2.erode(cleaned_mask, kernel, iterations=1)
        if eroded_mask.sum() > 0:
            cleaned_mask = eroded_mask

    # --- Deproject foreground pixels to 3D ---
    points, pixel_coords = deproject_masked(depth_m, cleaned_mask, intrinsics)
    if points.shape[0] < 5:
        return None

    # --- Defense-in-depth: strict per-point height re-check ---
    # The 2D mask used min_height_m once, then morphology may have grown
    # or bridged it. Re-verify each surviving point against its EXACT
    # per-pixel calibrated height with a stricter margin (1.5x or +4mm,
    # whichever is larger). This is what actually catches weighing-scale
    # surface pixels (e.g. a grated/perforated metal plate with uneven IR
    # return, or a slightly stale calibration baseline) that marginally
    # cleared the mask threshold and would otherwise get DBSCAN-clustered
    # together with the real parcel, inflating L/W and corrupting the OBB
    # orientation.
    if height_above_empty_m is not None:
        strict_floor_m = max(min_height_m * 1.5, min_height_m + 0.004)
        point_heights = height_above_empty_m[pixel_coords[:, 0], pixel_coords[:, 1]]
        keep_strict = point_heights >= strict_floor_m
        if np.count_nonzero(keep_strict) >= 5:
            points = points[keep_strict]
            pixel_coords = pixel_coords[keep_strict]
        # If fewer than 5 points survive the strict check, the candidate
        # was almost entirely marginal-height pixels (very likely scale
        # contamination, not a real thin parcel) -- fall through with the
        # original (looser) candidate rather than failing outright, since
        # a genuinely thin envelope can legitimately sit close to this
        # margin. The DBSCAN scale-center gating below is the next line
        # of defense in that case.

    # --- Fit plane for downstream height estimation only (no point removal) ---
    # Background subtraction already removed the scale surface.
    # Whatever foreground remains IS the parcel. Removing the dominant
    # plane here eats the flat top face of a box and leaves only noisy
    # side-wall fragments. Keep all points; pass plane to estimator.py.
    camera_origin = np.array([0.0, 0.0, 0.0])
    plane = segment_plane(
        points,
        distance_threshold=plane_distance_threshold,
        orient_towards=camera_origin,
    )
    plane_inlier_ratio = len(plane.inlier_indices) / points.shape[0]

    candidate_points = points
    candidate_pixels = pixel_coords

    # Too few points for DBSCAN to do anything meaningful (e.g. a thin
    # sliver of an envelope) -- skip clustering and use the whole blob
    # directly rather than failing.
    if candidate_points.shape[0] < dbscan_min_samples:
        return _whole_candidate_as_result(
            candidate_points, candidate_pixels, h, w, plane, plane_inlier_ratio
        )

    # --- Voxel downsample before DBSCAN ---
    down_points, voxel_id_per_point = voxel_downsample_with_index(candidate_points, voxel_size)

    # --- DBSCAN on the downsampled cloud ---
    labels = DBSCAN(eps=dbscan_eps, min_samples=max(3, dbscan_min_samples // 2)).fit_predict(down_points)

    valid_labels = labels[labels >= 0]
    if valid_labels.size == 0:
        # DBSCAN found no dense cluster at all -- typical for a sparse,
        # low-point-density irregular object. Fall back to the whole
        # blob instead of rejecting; it's already ROI-gated and was the
        # largest connected foreground component.
        return _whole_candidate_as_result(
            candidate_points, candidate_pixels, h, w, plane, plane_inlier_ratio
        )

    label_counts = np.bincount(valid_labels)

    # --- Multiple-parcel detection ---
    # Warehouse policy: exactly one parcel per transaction. Count clusters
    # that independently clear the "real object" bar (same fraction/count
    # floor used below to accept a cluster at all). If more than one does,
    # this is a multi-object scene -- do NOT let DBSCAN/scale-center
    # proximity silently auto-pick a winner. Flag it; the caller rejects
    # the measurement outright rather than guessing which parcel is real.
    #
    # The fraction/absolute-count floor alone is scene-size-agnostic: a
    # small residual ring of edge/flying-pixel points (see erosion above)
    # can clear a fixed `min_cluster_points` floor on its own even when
    # the rest of the scene is one large, dense parcel. A genuine second
    # parcel is comparable in size to the first, not a sliver of it -- so
    # also require a candidate cluster to be a meaningful fraction of the
    # *dominant* cluster's point count before counting it as a real,
    # independent parcel. This is tuned to the actual geometry of the
    # scene rather than a single hardcoded global threshold.
    dominant_count = int(label_counts.max())
    significant_clusters = int(np.sum(
        (
            (label_counts / down_points.shape[0] >= min_cluster_fraction) |
            (label_counts >= min_cluster_points)
        )
        & (label_counts >= min_cluster_relative_to_largest * dominant_count)
    ))
    multiple_parcels = significant_clusters > 1

    if scale_center is not None:
        # Pick the cluster whose 2D pixel centroid is closest to the
        # scale centre — this is the object on the weighing platform,
        # not a hand or background clutter that happens to be larger.
        sc_col, sc_row = scale_center  # scale_center is (x, y) = (col, row)
        best_label = None
        best_dist = float("inf")
        for lbl, count in enumerate(label_counts):
            if count == 0:
                continue
            # Map downsampled label back to candidate pixels
            down_label_per_point_tmp = labels[voxel_id_per_point]
            cluster_pixels = candidate_pixels[down_label_per_point_tmp == lbl]
            if cluster_pixels.shape[0] == 0:
                continue
            cen_row = cluster_pixels[:, 0].mean()
            cen_col = cluster_pixels[:, 1].mean()
            dist = np.hypot(cen_col - sc_col, cen_row - sc_row)
            if dist < best_dist:
                best_dist = dist
                best_label = lbl
        if best_label is None:
            return _whole_candidate_as_result(
                candidate_points, candidate_pixels, h, w, plane, plane_inlier_ratio
            )
        largest_label = best_label
    else:
        largest_label = int(np.argmax(label_counts))

    # Accept the cluster if it clears EITHER a fraction-of-cloud threshold
    # (good for dense box-shaped parcels) OR an absolute point-count floor
    # (good for small/thin/sparse irregular parcels, which will never
    # clear a fraction threshold tuned for dense clouds). Only reject if
    # it clears neither -- and even then, fall back to the whole blob
    # rather than failing outright, since a real object IS on the scale.
    cluster_frac = label_counts[largest_label] / down_points.shape[0]
    cluster_pts = label_counts[largest_label]
    if cluster_frac < min_cluster_fraction and cluster_pts < min_cluster_points:
        return _whole_candidate_as_result(
            candidate_points, candidate_pixels, h, w, plane, plane_inlier_ratio
        )

    # --- Map winning cluster back to full-resolution points ---
    down_label_per_point = labels[voxel_id_per_point]  # (N_candidate,)
    keep = down_label_per_point == largest_label

    final_points = candidate_points[keep]
    final_pixels = candidate_pixels[keep]

    if final_points.shape[0] == 0:
        return None

    final_mask = np.zeros((h, w), dtype=np.uint8)
    final_mask[final_pixels[:, 0], final_pixels[:, 1]] = 255

    return SegmentationResult(
        points=final_points,
        pixel_coords=final_pixels,
        mask=final_mask,
        bed_plane=plane,
        plane_inlier_ratio=plane_inlier_ratio,
        multiple_parcels=multiple_parcels,
    )
