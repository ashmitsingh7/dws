"""
geometry/pointcloud.py

Stage 6 — Point Cloud Cleaning, plus shared point-cloud utilities
(deprojection from depth+mask, voxel downsampling) used by the
segmentation stage.
"""

from __future__ import annotations
import numpy as np
import open3d as o3d

from ..capture.frame import Intrinsics


def deproject_masked(
    depth_m: np.ndarray,
    mask: np.ndarray,
    intrinsics: Intrinsics,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Back-project all pixels where mask > 0 and depth_m > 0 into camera-space
    3D points.

    Returns:
        points: (N, 3) float32, meters
        pixel_coords: (N, 2) int32, (row, col) of each point, for mapping
            results back onto the 2D image (e.g. for visualization).
    """
    valid = (mask > 0) & (depth_m > 0)
    rows, cols = np.nonzero(valid)
    if rows.size == 0:
        return np.zeros((0, 3), dtype=np.float32), np.zeros((0, 2), dtype=np.int32)

    depths = depth_m[rows, cols]
    points = intrinsics.deproject(cols.astype(np.float64), rows.astype(np.float64), depths.astype(np.float64))
    pixel_coords = np.stack([rows, cols], axis=-1).astype(np.int32)
    return points.astype(np.float32), pixel_coords


def to_o3d(points: np.ndarray) -> o3d.geometry.PointCloud:
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points.astype(np.float64))
    return pcd


def voxel_downsample_with_index(
    points: np.ndarray, voxel_size: float
) -> tuple[np.ndarray, np.ndarray]:
    """
    Downsample `points` to one representative point per voxel, and return
    a mapping from each ORIGINAL point to its voxel's representative index
    in the downsampled array. This lets clustering run cheaply on the
    downsampled cloud while still recovering full-resolution membership
    for the winning cluster.

    Returns:
        down_points: (M, 3) float32 -- one point per occupied voxel
            (the centroid of points falling in that voxel)
        voxel_id_per_point: (N,) int64 -- for each original point, the
            index into down_points of its voxel
    """
    if points.shape[0] == 0:
        return np.zeros((0, 3), dtype=np.float32), np.zeros((0,), dtype=np.int64)

    # Offset so all coordinates are non-negative before integer voxel
    # indexing -- avoids any sign-related bit-packing issues.
    mins = points.min(axis=0)
    shifted = points - mins
    voxel_idx = np.floor(shifted / voxel_size).astype(np.int64)  # (N, 3)

    # Unique voxel coordinates -> representative index per point.
    # np.unique with axis=0 + return_inverse gives exactly this mapping.
    unique_voxels, voxel_id_per_point = np.unique(voxel_idx, axis=0, return_inverse=True)
    voxel_id_per_point = voxel_id_per_point.reshape(-1)

    # Centroid of original points per voxel (more representative than an
    # arbitrary single sample).
    num_voxels = unique_voxels.shape[0]
    sums = np.zeros((num_voxels, 3), dtype=np.float64)
    counts = np.zeros((num_voxels,), dtype=np.int64)
    np.add.at(sums, voxel_id_per_point, points.astype(np.float64))
    np.add.at(counts, voxel_id_per_point, 1)
    down_points = (sums / counts[:, None]).astype(np.float32)

    return down_points, voxel_id_per_point


def remove_statistical_outlier(
    points: np.ndarray, nb_neighbors: int = 20, std_ratio: float = 2.0
) -> tuple[np.ndarray, np.ndarray]:
    """
    Open3D statistical outlier removal. Returns (filtered_points, kept_indices).
    """
    if points.shape[0] == 0:
        return points, np.zeros((0,), dtype=np.int64)
    pcd = to_o3d(points)
    _, ind = pcd.remove_statistical_outlier(nb_neighbors=nb_neighbors, std_ratio=std_ratio)
    ind = np.asarray(ind, dtype=np.int64)
    return points[ind], ind


def remove_radius_outlier(
    points: np.ndarray, nb_points: int = 8, radius: float = 0.02
) -> tuple[np.ndarray, np.ndarray]:
    """
    Open3D radius outlier removal. Returns (filtered_points, kept_indices).
    """
    if points.shape[0] == 0:
        return points, np.zeros((0,), dtype=np.int64)
    pcd = to_o3d(points)
    _, ind = pcd.remove_radius_outlier(nb_points=nb_points, radius=radius)
    ind = np.asarray(ind, dtype=np.int64)
    return points[ind], ind


def clean_point_cloud(
    points: np.ndarray,
    statistical_nb_neighbors: int = 20,
    statistical_std_ratio: float = 2.0,
    radius_nb_points: int = 8,
    radius: float = 0.02,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Stage 6 full pipeline: statistical outlier removal, then radius outlier
    removal. Returns (cleaned_points, kept_indices_into_original).

    Neighbor-count parameters are tuned for a dense box top face (thousands
    of points). For sparse/thin irregular parcels (a flat envelope, a small
    object with a low point return), demanding 20 neighbors within a fixed
    radius is unmeetable by construction and silently strips the whole
    cloud. Scale both neighbor counts down for small clouds so cleaning
    still removes genuine speckle without erasing legitimate sparse data.
    """
    n = points.shape[0]
    nb_neighbors = min(statistical_nb_neighbors, max(4, n // 4))
    nb_points = min(radius_nb_points, max(2, n // 8))

    pts1, ind1 = remove_statistical_outlier(points, nb_neighbors, statistical_std_ratio)
    if pts1.shape[0] == 0:
        return pts1, ind1
    pts2, ind2_local = remove_radius_outlier(pts1, nb_points, radius)
    kept_indices = ind1[ind2_local]
    return pts2, kept_indices
