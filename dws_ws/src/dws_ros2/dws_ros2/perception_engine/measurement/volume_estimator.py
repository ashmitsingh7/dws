"""
measurement/volume_estimator.py

Real volume estimation for irregular objects by voxel column integration.

For a regular box, L*W*H and voxel volume agree closely.
For an irregular object (poly bag, rounded parcel, L-shaped item), the
voxel method gives the actual occupied volume — not the bounding-box
overestimate.

Method:
  1. Divide the XY footprint into a grid of small cells (voxel_size_m square).
  2. For each occupied cell, compute the height column: how far that 
     column of the parcel extends from the scale surface down to the
     closest sensor return (i.e., the tallest part of the parcel in that
     cell column).
  3. Sum all column volumes: cell_area * column_height.

This correctly handles:
  - Rounded/lumpy tops (each cell gets its own local height)
  - L-shaped or irregular footprints (empty cells contribute zero)
  - Overhangs don't exist in depth sensor data, so no double-counting

The result is comparable to a water-displacement volume for convex objects
and gives the "shipping volume" (actual space occupied) for irregular shapes.
"""

from __future__ import annotations
import numpy as np


def compute_voxel_volume(
    parcel_points: np.ndarray,
    scale_depth_m: float,
    voxel_size_m: float = 0.003,   # 3mm cells — fine enough for typical parcels
    min_height_m: float = 0.003,   # ignore cells where the column is < 3mm
) -> tuple[float, float]:
    """
    Compute the real irregular volume of the parcel by column integration.

    Args:
        parcel_points: (N, 3) float32, cleaned parcel point cloud in meters.
                       Z = depth from camera (smaller Z = closer to camera = taller).
        scale_depth_m: depth of the empty scale surface from camera.
                       Points closer than this (smaller Z) are above the scale.
        voxel_size_m:  XY cell size in meters. 3mm gives good resolution
                       without too much noise sensitivity.
        min_height_m:  minimum column height to include (rejects single-pixel
                       noise spikes that may have snuck through outlier removal).

    Returns:
        (volume_m3, footprint_area_m2)
    """
    if parcel_points.shape[0] == 0:
        return 0.0, 0.0

    x = parcel_points[:, 0]
    y = parcel_points[:, 1]
    z = parcel_points[:, 2]

    # Bin X and Y into grid cells
    x_min, x_max = x.min(), x.max()
    y_min, y_max = y.min(), y.max()

    if (x_max - x_min) < voxel_size_m or (y_max - y_min) < voxel_size_m:
        # Cloud too small for grid — fall back to simple average height * hull area
        avg_height = float(np.clip(scale_depth_m - np.percentile(z, 5), 0.0, 5.0))
        return 0.0, 0.0   # let caller fall back to bounding-box volume

    ix = np.floor((x - x_min) / voxel_size_m).astype(np.int64)
    iy = np.floor((y - y_min) / voxel_size_m).astype(np.int64)

    nx = int(ix.max()) + 1
    ny = int(iy.max()) + 1

    # Vectorized per-cell min-Z (top-of-parcel depth in each XY column).
    # Flatten (ix, iy) into a single cell id, then use np.minimum.at for a
    # scatter-min reduction instead of a Python-level per-point loop (the
    # old loop was O(N) in pure Python -- tens of ms per 30-40k pt frame on
    # a dev laptop, materially worse on a Pi 5).
    cell_id = ix * ny + iy
    n_cells = nx * ny
    cell_min_z = np.full(n_cells, np.inf, dtype=np.float64)
    np.minimum.at(cell_min_z, cell_id, z)

    occupied = np.isfinite(cell_min_z)
    col_height = scale_depth_m - cell_min_z[occupied]
    valid = col_height >= min_height_m

    cell_area = voxel_size_m * voxel_size_m
    total_volume = float(np.sum(col_height[valid]) * cell_area)
    footprint_area = float(np.count_nonzero(valid) * cell_area)

    return total_volume, footprint_area
