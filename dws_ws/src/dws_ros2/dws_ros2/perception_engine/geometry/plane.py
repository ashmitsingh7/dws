"""
geometry/plane.py

Stage 4 — Plane Segmentation (kept from PHANTOM, via Open3D RANSAC).

Plane convention used throughout this engine:
    a*x + b*y + c*z + d = 0,  with (a, b, c) normalized to unit length.
    signed_distance(p) = a*x + b*y + c*z + d
    A point is "above" the plane (closer to camera, i.e. smaller z when
    the plane is roughly perpendicular to the optical axis) when its
    signed distance has the sign chosen by `orient_normal_towards`.

Every stage that fits or uses a plane MUST go through this module so the
sign convention can never drift between segmentation, estimator, and
confidence stages -- that inconsistency caused real bugs in an earlier
iteration of this engine.
"""

from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import open3d as o3d


@dataclass
class Plane:
    a: float
    b: float
    c: float
    d: float
    inlier_indices: np.ndarray  # indices into the input cloud

    @property
    def normal(self) -> np.ndarray:
        return np.array([self.a, self.b, self.c], dtype=np.float64)

    def signed_distance(self, points: np.ndarray) -> np.ndarray:
        """points: (N, 3) -> (N,) signed distance to this plane."""
        return points @ self.normal + self.d

    def orient_towards(self, reference_point: np.ndarray) -> "Plane":
        """
        Flip (a,b,c,d) sign if needed so that signed_distance(reference_point) > 0.
        Use this to pin down "positive = towards camera" consistently
        regardless of which side RANSAC happened to return the normal on.
        """
        dist = self.signed_distance(reference_point.reshape(1, 3))[0]
        if dist < 0:
            return Plane(-self.a, -self.b, -self.c, -self.d, self.inlier_indices)
        return self


def segment_plane(
    points: np.ndarray,
    distance_threshold: float = 0.004,
    ransac_n: int = 3,
    num_iterations: int = 1000,
    orient_towards: np.ndarray | None = None,
) -> Plane:
    """
    Fit the dominant plane in `points` (N, 3 meters) via Open3D RANSAC.

    distance_threshold: meters. 4mm default suits a D455 at typical
        0.5-1.5m working distance; tighten/loosen per real noise levels.
    orient_towards: optional reference point (e.g. camera origin
        np.array([0,0,0])) to pin the plane's sign convention.
    """
    if points.shape[0] < ransac_n:
        raise ValueError(f"Need at least {ransac_n} points to fit a plane, got {points.shape[0]}")

    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points.astype(np.float64))

    plane_model, inliers = pcd.segment_plane(
        distance_threshold=distance_threshold,
        ransac_n=ransac_n,
        num_iterations=num_iterations,
    )
    a, b, c, d = plane_model
    plane = Plane(a, b, c, d, inlier_indices=np.asarray(inliers))

    if orient_towards is not None:
        plane = plane.orient_towards(np.asarray(orient_towards, dtype=np.float64))

    return plane
