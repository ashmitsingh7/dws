"""
visualization/viewer.py

Stage 12 — Debug Visualization, LIVE dual-window mode.

Two OpenCV windows (Open3D is skipped — it fails on Wayland/GLEW):
  1. "RealSense Perception Engine - Live": live RGB feed; on capture,
     freezes with parcel mask outline + dimension text overlaid.
  2. "Point Cloud - 3D View": on capture, renders a height-colored
     oblique 3D projection of the cleaned parcel point cloud + the
     actual measured box wireframe (not a flat top-down silhouette --
     this reads as genuinely 3D, including steps/lumps on irregular or
     stacked objects). Works on any display server (X11, Wayland,
     headless+VNC) since it's pure OpenCV, no Open3D GUI involved.

Controls: SPACE = capture & measure, Q = quit.
"""

from __future__ import annotations
import numpy as np
import cv2

from ..core.measurement_result import MeasurementResult

TRIGGER_KEY = 32      # SPACE
QUIT_KEY = ord("q")

WINDOW_2D   = "RealSense Perception Engine - Live"
WINDOW_TOP  = "Point Cloud - 3D View"

_TOP_SIZE = 540   # px, square canvas for the top-down view


def _largest_contour(mask: np.ndarray):
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    return max(contours, key=cv2.contourArea)


def _project_rect_corners(corners_3d: np.ndarray, intrinsics) -> np.ndarray | None:
    """
    Project the 4 3D rect corners (in camera space, meters) back to 2D pixel
    coordinates using the camera intrinsics. Returns (4, 2) int32 array or None.
    """
    try:
        fx = intrinsics.fx
        fy = intrinsics.fy
        cx = intrinsics.ppx
        cy = intrinsics.ppy
        pixels = []
        for p in corners_3d:
            if p[2] < 1e-4:
                return None
            u = int(fx * p[0] / p[2] + cx)
            v = int(fy * p[1] / p[2] + cy)
            pixels.append([u, v])
        return np.array(pixels, dtype=np.int32)
    except Exception:
        return None


def draw_overlay(color_bgr: np.ndarray, result: MeasurementResult, intrinsics=None) -> np.ndarray:
    """
    Draw a clean rotated bounding box (not the raw jagged mask contour)
    around the parcel, plus a zoomed-in inset crop so the parcel is
    actually visible at a useful resolution, plus dimension/confidence
    text.
    """
    vis = color_bgr.copy()

    if result.success and result.mask is not None:
        contour = _largest_contour(result.mask)
        if contour is not None and len(contour) >= 3:
            # Faint raw contour (debug reference) ...
            cv2.drawContours(vis, [contour], -1, (0, 130, 0), 1, cv2.LINE_AA)

        # --- Preferred: project the actual 3D measurement rect to 2D ---
        # This IS the exact rectangle the L/W numbers come from.
        projected_box = None
        if result.rect_corners_3d is not None and intrinsics is not None:
            projected_box = _project_rect_corners(result.rect_corners_3d, intrinsics)

        if projected_box is not None:
            # Draw the 3D-derived rect in bright green
            cv2.drawContours(vis, [projected_box], -1, (0, 255, 0), 2, cv2.LINE_AA)
            for pt in projected_box:
                cv2.circle(vis, tuple(pt), 4, (0, 255, 255), -1, cv2.LINE_AA)
            # Label as "PARCEL" at the rect centroid
            cx_label = int(projected_box[:, 0].mean())
            cy_label = int(projected_box[:, 1].mean())
            cv2.putText(vis, "PARCEL (zoomed)", (cx_label - 50, cy_label + 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 100), 1, cv2.LINE_AA)
        elif contour is not None and len(contour) >= 3:
            # Fallback: draw a 2D minAreaRect from the mask contour
            rect = cv2.minAreaRect(contour)
            box = cv2.boxPoints(rect).astype(np.int32)
            cv2.drawContours(vis, [box], -1, (0, 255, 0), 2, cv2.LINE_AA)
            for pt in box:
                cv2.circle(vis, tuple(pt), 4, (0, 255, 255), -1, cv2.LINE_AA)

        if contour is not None and len(contour) >= 3:
            _draw_zoom_inset(vis, color_bgr, contour, result.mask)

    lines = []
    if result.success:
        lines.append(f"L: {result.length_mm:.1f} mm")
        lines.append(f"W: {result.width_mm:.1f} mm")
        lines.append(f"H: {result.height_mm:.1f} mm")
        vol_label = "Vol*" if result.shape_type == "irregular" else "Vol"
        lines.append(f"{vol_label}: {result.volume_mm3 / 1e6:.3f} L")
        lines.append(f"Confidence: {result.confidence:.1f}%")
        lines.append(f"Time: {result.processing_time_ms:.0f} ms")
        if result.shape_type == "irregular":
            lines.append(f"Shape: irregular (footprint {result.footprint_area_mm2 / 100.0:.1f} cm2)")
        if result.used_secondary_top:
            lines.append("Stacked top detected (height = tallest surface)")
        color = (0, 255, 0)
    else:
        lines.append("MEASUREMENT FAILED")
        lines.append(str(result.failure_reason))
        color = (0, 0, 255)

    y = 40
    for line in lines:
        cv2.putText(vis, line, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2, cv2.LINE_AA)
        y += 35

    cv2.putText(
        vis, "SPACE = capture/measure   Q = quit",
        (20, vis.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1, cv2.LINE_AA,
    )
    return vis


def _draw_zoom_inset(vis: np.ndarray, color_bgr: np.ndarray, contour: np.ndarray, mask: np.ndarray):
    """
    Crop tightly around the parcel (with margin), upscale it, and paste
    it as an inset in the top-right corner with its own mask outline --
    a much clearer view of the parcel than the small region it occupies
    in the full 1280x720 frame.
    """
    x, y, w, h = cv2.boundingRect(contour)
    if w < 4 or h < 4:
        return

    margin = int(max(w, h) * 0.25) + 10
    fx1 = max(0, x - margin)
    fy1 = max(0, y - margin)
    fx2 = min(color_bgr.shape[1], x + w + margin)
    fy2 = min(color_bgr.shape[0], y + h + margin)

    crop = color_bgr[fy1:fy2, fx1:fx2].copy()
    crop_mask = mask[fy1:fy2, fx1:fx2]
    if crop.shape[0] < 2 or crop.shape[1] < 2:
        return

    # Draw the box outline onto the crop too, in crop-local coordinates.
    crop_contour, _ = cv2.findContours(crop_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if crop_contour:
        biggest = max(crop_contour, key=cv2.contourArea)
        rect = cv2.minAreaRect(biggest)
        box = cv2.boxPoints(rect).astype(np.int32)
        cv2.drawContours(crop, [box], -1, (0, 255, 0), 2, cv2.LINE_AA)

    inset_w = 360
    scale = inset_w / crop.shape[1]
    inset_h = int(crop.shape[0] * scale)
    inset_h = min(inset_h, vis.shape[0] - 40)
    if inset_h < 10:
        return
    inset = cv2.resize(crop, (inset_w, inset_h), interpolation=cv2.INTER_CUBIC)

    ix1 = vis.shape[1] - inset_w - 20
    iy1 = 20
    ix2, iy2 = ix1 + inset_w, iy1 + inset_h

    cv2.rectangle(vis, (ix1 - 4, iy1 - 4), (ix2 + 4, iy2 + 26), (30, 30, 30), -1)
    vis[iy1:iy2, ix1:ix2] = inset
    cv2.rectangle(vis, (ix1, iy1), (ix2, iy2), (0, 255, 0), 1)
    cv2.putText(vis, "PARCEL (zoomed)", (ix1, iy2 + 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)


def _isometric_rotation(yaw_deg: float = 38.0, pitch_deg: float = 24.0) -> np.ndarray:
    """
    Rotation matrix that turns a flat top-down silhouette into a
    recognizable 3/4 oblique 3D view. Camera-frame convention here:
    X right, Y down, Z forward (depth/away-from-camera).
    """
    yaw, pitch = np.radians(yaw_deg), np.radians(pitch_deg)
    cy, sy = np.cos(yaw), np.sin(yaw)
    cp, sp = np.cos(pitch), np.sin(pitch)
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    return Rx @ Ry


def _box_corners_from_result(result: MeasurementResult) -> np.ndarray | None:
    """
    Reconstruct the actual measured 3D box (8 corners, meters, camera
    frame) from the 4 measured top-face corners + measured height --
    i.e. exactly the box the L/W/H numbers describe, not Open3D's
    independent (and possibly looser/outlier-inflated) OBB fit.
    """
    if result.rect_corners_3d is None or result.rect_corners_3d.shape[0] != 4:
        return None
    top = result.rect_corners_3d.astype(np.float64)
    v1, v2 = top[1] - top[0], top[3] - top[0]
    normal = np.cross(v1, v2)
    n_norm = np.linalg.norm(normal)
    if n_norm < 1e-9:
        return None
    normal /= n_norm
    # "Up" (top -> camera) should be roughly -Z; flip if RANSAC/PCA gave
    # us the opposite-facing normal.
    if normal[2] > 0:
        normal = -normal
    height_m = max(result.height_mm, 0.0) / 1000.0
    bottom = top - normal * height_m  # away from camera = towards the table
    return np.concatenate([top, bottom], axis=0)  # (8, 3): [top x4, bottom x4]


_BOX_EDGES = [
    (0, 1), (1, 2), (2, 3), (3, 0),   # top face
    (4, 5), (5, 6), (6, 7), (7, 4),   # bottom face
    (0, 4), (1, 5), (2, 6), (3, 7),   # verticals
]


def _draw_3d_view(result: MeasurementResult) -> np.ndarray:
    """
    Render a real oblique 3D view of the parcel point cloud (colored by
    height, near = warm / far = cool) plus a wireframe of the actual
    measured box -- an objective, depth-readable 3D view of the parcel,
    including irregular/stacked shapes where the silhouette alone
    (a flat top-down projection) can't show the step/lump.
    """
    canvas = np.zeros((_TOP_SIZE, _TOP_SIZE, 3), dtype=np.uint8)

    if not result.success or result.parcel_cloud is None or result.parcel_cloud.shape[0] == 0:
        cv2.putText(canvas, "No point cloud", (20, _TOP_SIZE // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (100, 100, 100), 2)
        return canvas

    pts = result.parcel_cloud.astype(np.float64)  # (N, 3) meters, camera frame
    centroid = pts.mean(axis=0)
    centered = pts - centroid

    R = _isometric_rotation()
    rotated = centered @ R.T  # (N, 3): [screen_x, screen_y, depth_for_sort]

    box_corners = _box_corners_from_result(result)
    rotated_box = (box_corners - centroid) @ R.T if box_corners is not None else None

    # Fit everything (points + box) into the canvas together so the
    # wireframe box never clips outside the frame.
    all_xy = rotated[:, :2]
    if rotated_box is not None:
        all_xy = np.concatenate([all_xy, rotated_box[:, :2]], axis=0)

    margin = 50
    draw_size = _TOP_SIZE - 2 * margin
    x_min, x_max = all_xy[:, 0].min(), all_xy[:, 0].max()
    y_min, y_max = all_xy[:, 1].min(), all_xy[:, 1].max()
    span = max(x_max - x_min, y_max - y_min, 1e-4)
    scale = draw_size / span

    def to_px(xy: np.ndarray) -> np.ndarray:
        px = (xy[:, 0] - x_min) * scale + margin
        py = (xy[:, 1] - y_min) * scale + margin
        return np.stack([px, py], axis=1)

    pts_px = to_px(rotated[:, :2])

    # Color by original depth: smaller camera-Z = closer to camera = a
    # physically higher point (camera looks down at the scale). Warm
    # (red/yellow) = high points, cool (blue) = low/base points.
    z = pts[:, 2]
    z_min, z_max = z.min(), z.max()
    z_span = max(z_max - z_min, 1e-6)
    height_norm = ((z_max - z) / z_span * 255.0).astype(np.uint8)  # high pts -> 255

    # Painter's algorithm: draw farthest-in-rotated-view points first so
    # nearer points correctly occlude them, reinforcing the 3D read.
    order = np.argsort(-rotated[:, 2])
    max_draw = 9000
    if order.shape[0] > max_draw:
        order = order[np.linspace(0, order.shape[0] - 1, max_draw).astype(np.int64)]

    color_lut = cv2.applyColorMap(np.arange(256, dtype=np.uint8).reshape(-1, 1), cv2.COLORMAP_TURBO).reshape(-1, 3)
    for i in order:
        x_i, y_i = int(pts_px[i, 0]), int(pts_px[i, 1])
        bgr = tuple(int(c) for c in color_lut[height_norm[i]])
        cv2.circle(canvas, (x_i, y_i), 2, bgr, -1, cv2.LINE_AA)

    # Wireframe of the actual measured box.
    if rotated_box is not None:
        box_px = to_px(rotated_box[:, :2]).astype(np.int32)
        for a, b in _BOX_EDGES:
            cv2.line(canvas, tuple(box_px[a]), tuple(box_px[b]), (0, 255, 0), 2, cv2.LINE_AA)
        for p in box_px[:4]:  # highlight top-face corners
            cv2.circle(canvas, tuple(p), 4, (0, 255, 255), -1, cv2.LINE_AA)

    cv2.putText(canvas, f"L={result.length_mm:.0f}  W={result.width_mm:.0f}  H={result.height_mm:.0f} mm",
                (8, _TOP_SIZE - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(canvas, "3D VIEW  (warm = high, cool = low)", (8, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 180, 180), 1, cv2.LINE_AA)
    if result.used_secondary_top:
        cv2.putText(canvas, "stacked-top corrected", (8, 42),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 255), 1, cv2.LINE_AA)

    return canvas


class LiveDualViewer:
    """
    Two OpenCV windows: live 2D overlay + top-down point cloud projection.
    No Open3D — avoids Wayland/GLEW failures entirely.
    """

    def __init__(self):
        cv2.namedWindow(WINDOW_2D, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW_2D, 960, 540)

        cv2.namedWindow(WINDOW_TOP, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW_TOP, _TOP_SIZE, _TOP_SIZE)
        cv2.moveWindow(WINDOW_TOP, 980, 50)

        # Show blank top-view on startup
        blank = np.zeros((_TOP_SIZE, _TOP_SIZE, 3), dtype=np.uint8)
        cv2.putText(blank, "Waiting for capture...", (20, _TOP_SIZE // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (100, 100, 100), 1)
        cv2.imshow(WINDOW_TOP, blank)

    def show_live_frame(self, color_bgr: np.ndarray) -> int:
        cv2.imshow(WINDOW_2D, color_bgr)
        key = cv2.waitKey(1) & 0xFF
        return key

    def show_result(self, color_bgr: np.ndarray, result: MeasurementResult, intrinsics=None):
        overlay = draw_overlay(color_bgr, result, intrinsics=intrinsics)
        cv2.imshow(WINDOW_2D, overlay)

        top = _draw_3d_view(result)
        cv2.imshow(WINDOW_TOP, top)

        cv2.waitKey(1)

    def poll_3d(self):
        pass  # no-op, kept for API compatibility with measure.py

    def close(self):
        cv2.destroyAllWindows()
