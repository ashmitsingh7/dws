"""
measurement/obb.py

Stage 7 — Oriented Bounding Box (instead of axis-aligned).
Stage 8 — Corner Refinement via PCA + Convex Hull + minAreaRect.

Approach:
  1. Fit the dominant top-face plane of the parcel (already known from
     segmentation's residual, but we re-fit on the final cleaned cloud
     for accuracy) and project points onto that plane's local 2D frame
     defined by its principal axes (PCA).
  2. Take the convex hull of the projected 2D points.
  3. Fit a minimum-area rectangle to the hull -- this is far more robust
     to rotated cartons than an axis-aligned bbox, and is the standard
     approach industrial scanners use for L/W of rotated boxes.
  4. Length/width come from the minAreaRect; height is handled
     separately in estimator.py (plane-to-plane distance, not raw
     z-extent) for stability.
"""

from __future__ import annotations
from dataclasses import dataclass
import contextlib
import os
import numpy as np
import cv2
import open3d as o3d

# Open3D's own logger -- doesn't silence qhull's native C-level prints, but
# cheap to also set correctly.
o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)


@contextlib.contextmanager
def _suppress_native_stdio():
    """
    Qhull (called internally by Open3D's get_oriented_bounding_box) writes
    its precision warnings directly to the process's real stdout/stderr
    file descriptors via C printf -- this bypasses Python's sys.stdout
    entirely, so logging.disable() or redirecting sys.stderr does nothing.
    The only way to silence it is to redirect the actual OS-level file
    descriptors for the duration of the call. Purely cosmetic (the
    warning is harmless and doesn't affect the result) -- this just keeps
    your terminal output readable.
    """
    try:
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        yield
        return
    saved_stdout_fd = os.dup(1)
    saved_stderr_fd = os.dup(2)
    try:
        os.dup2(devnull_fd, 1)
        os.dup2(devnull_fd, 2)
        yield
    finally:
        os.dup2(saved_stdout_fd, 1)
        os.dup2(saved_stderr_fd, 2)
        os.close(saved_stdout_fd)
        os.close(saved_stderr_fd)
        os.close(devnull_fd)


@dataclass
class OBBResult:
    length_m: float
    width_m: float
    center_3d: np.ndarray       # (3,) point on the top-face plane at the rect center
    rotation_3d: np.ndarray     # (3, 3) rotation: columns are [u_axis, v_axis, normal]
    obb_o3d: object             # open3d.geometry.OrientedBoundingBox, for visualization
    hull_points_2d: np.ndarray  # (K, 2) convex hull in the local 2D frame, for debugging
    rect_corners_3d: np.ndarray  # (4, 3) the ACTUAL measured minAreaRect's corners,
                                  # back-projected onto the top-face plane in camera
                                  # space. This is what visualization should draw --
                                  # it's the exact rectangle the L/W numbers come from,
                                  # not Open3D's independent (and looser) OBB fit.


def _pca_basis(points: np.ndarray, plane_normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Build an orthonormal 2D basis (u, v) spanning the plane defined by
    plane_normal, aligned with the in-plane principal axes of `points`
    (PCA on the points' projection onto the plane). Returns (u, v), each
    a unit vector in 3D.
    """
    normal = plane_normal / np.linalg.norm(plane_normal)

    # Arbitrary vector not parallel to normal, to seed an initial in-plane basis.
    seed = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u0 = np.cross(normal, seed)
    u0 /= np.linalg.norm(u0)
    v0 = np.cross(normal, u0)

    centroid = points.mean(axis=0)
    centered = points - centroid
    proj_u = centered @ u0
    proj_v = centered @ v0
    proj_2d = np.stack([proj_u, proj_v], axis=-1)

    # PCA on the 2D projection to align u, v with the parcel's actual
    # principal axes (so the minAreaRect aligns naturally with the box edges).
    cov = np.cov(proj_2d.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    order = np.argsort(eigvals)[::-1]
    eigvecs = eigvecs[:, order]

    u = eigvecs[0, 0] * u0 + eigvecs[1, 0] * v0
    v = eigvecs[0, 1] * u0 + eigvecs[1, 1] * v0
    u /= np.linalg.norm(u)
    v /= np.linalg.norm(v)
    return u, v


def _build_obb_from_rect(
    centroid: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    normal: np.ndarray,
    rect_cx: float,
    rect_cy: float,
    rect_w: float,
    rect_h: float,
    points: np.ndarray,
) -> o3d.geometry.OrientedBoundingBox:
    """
    Construct an OrientedBoundingBox directly from our own PCA axes +
    minAreaRect, without going through Open3D's qhull-based fit. Used as
    a fallback when the point cloud is too close to perfectly planar for
    qhull's convex hull computation to succeed.
    """
    center_3d = centroid + rect_cx * u + rect_cy * v
    extent_thickness = max(np.ptp(points @ normal), 1e-4)  # z-extent along the plane normal

    R = np.stack([u, v, normal], axis=1)
    extent = np.array([rect_w, rect_h, extent_thickness], dtype=np.float64)

    obb = o3d.geometry.OrientedBoundingBox(center_3d.astype(np.float64), R, extent)
    return obb



def compute_oriented_bbox(
    points: np.ndarray,
    plane_normal: np.ndarray,
) -> OBBResult:
    """
    points: (N, 3) float32 meters -- cleaned parcel point cloud (top
        face + visible side walls).
    plane_normal: (3,) the top-face plane's normal (from Stage 4/9's
        plane fit), used to define the projection plane for PCA + hull.
    """
    centroid = points.mean(axis=0)
    u, v = _pca_basis(points, plane_normal)

    centered = points - centroid
    proj_u = centered @ u
    proj_v = centered @ v
    proj_2d = np.stack([proj_u, proj_v], axis=-1).astype(np.float32)

    # Convex hull (Stage 8) -- robust to noisy boundary points.
    hull = cv2.convexHull(proj_2d)
    hull_points_2d = hull.reshape(-1, 2)

    # Minimum-area rectangle on the hull -- robust to rotated cartons.
    rect = cv2.minAreaRect(hull_points_2d)
    (rect_cx, rect_cy), (rect_w, rect_h), angle_deg = rect

    length_m = float(max(rect_w, rect_h))
    width_m = float(min(rect_w, rect_h))

    center_3d = centroid + rect_cx * u + rect_cy * v
    normal_unit = plane_normal / np.linalg.norm(plane_normal)
    rotation_3d = np.stack([u, v, normal_unit], axis=1)  # columns = axes

    # Back-project the 4 actual rect corners (in the local u/v plane frame)
    # into camera-space 3D. This is the exact rectangle the L/W numbers
    # above come from -- used by visualization instead of Open3D's
    # independent OBB fit, which can be looser (includes side-wall/edge
    # noise, no trim factor, and -- if its 8 corners are projected for a
    # 2D top-down view -- introduces parallax from the bottom face).
    box_pts_2d = cv2.boxPoints(rect)  # (4, 2) in the (u, v) local frame
    rect_corners_3d = centroid + box_pts_2d[:, 0:1] * u + box_pts_2d[:, 1:2] * v

    # Open3D OBB for visualization -- built directly from the full 3D cloud
    # using its own oriented bounding box fit, which is independent of our
    # 2D rect (kept for visual sanity-checking against the measured rect).
    #
    # get_oriented_bounding_box() internally runs a convex-hull / qhull
    # computation that fails ("Initial simplex is flat") when the cloud
    # is degenerate in one dimension -- e.g. a perfectly planar top face
    # with near-zero z-variance (real sensor noise normally prevents
    # this, but it's not implausible on a very flat, low-noise capture).
    # Fall back to constructing the OBB directly from our own 2D rect +
    # known thickness instead of relying on qhull in that case.
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points.astype(np.float64))
    try:
        with _suppress_native_stdio():
            obb_o3d = pcd.get_oriented_bounding_box(robust=True)
        obb_o3d.color = (1.0, 0.0, 0.0)
    except RuntimeError:
        obb_o3d = _build_obb_from_rect(
            centroid, u, v, normal_unit, rect_cx, rect_cy, rect_w, rect_h, points
        )
        obb_o3d.color = (1.0, 0.0, 0.0)

    return OBBResult(
        length_m=length_m,
        width_m=width_m,
        center_3d=center_3d,
        rotation_3d=rotation_3d,
        obb_o3d=obb_o3d,
        hull_points_2d=hull_points_2d,
        rect_corners_3d=rect_corners_3d.astype(np.float32),
    )
