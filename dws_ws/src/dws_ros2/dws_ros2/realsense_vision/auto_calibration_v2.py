import numpy as np
import cv2
import os
import logging

from dws_ros2.paths import CALIBRATION_DIR as SAVE_DIR

# ============================================================
# REALSENSE AUTO CALIBRATION V2
# HMI DRIVEN + HEADLESS + INDUSTRIAL STABLE
# Callable module — camera owned by VisionNodeRealSense
# ============================================================

log = logging.getLogger(
    "auto_calibration_v2"
)

# ============================================================
# CONFIG
# ============================================================

CAPTURE_FRAMES = 120

OUTER_RADIUS = 180
INNER_RADIUS = 90

PLANE_TOLERANCE_MM = 8

ROI_HALF_SIZE = 260


class AutoCalibrationV2:

    # ========================================================
    # INIT
    # ========================================================

    def __init__(self, camera):

        self.camera = camera

        os.makedirs(
            SAVE_DIR,
            exist_ok=True
        )

    # ========================================================
    # RUN
    # ========================================================

    def run(self):

        # ====================================================
        # WARMUP
        # ====================================================

        log.info(
            "[CAL] Warming up camera unit..."
        )

        for _ in range(80):

            try:
                self.camera.get_frames()

            except:
                continue

        # ====================================================
        # CAPTURE EMPTY SCENE
        # No operator alignment required — HMI driven.
        # Operator must position scale before triggering
        # calibration from the HMI. Capture begins immediately.
        # ====================================================

        log.info(
            "[CAL] Starting capture — "
            "ensure scale is positioned before triggering"
        )

        log.info(
            f"[CAL] Capturing {CAPTURE_FRAMES} frames..."
        )

        depth_stack = []

        color_image = None

        for i in range(CAPTURE_FRAMES):

            try:

                frames = self.camera.get_frames()

                if frames is None:
                    continue

                depth_image = frames.get("depth")
                color_image = frames.get("color")

                if depth_image is None:
                    continue

                if color_image is None:
                    continue

                depth_stack.append(
                    depth_image.astype(np.float32)
                )

                if (i + 1) % 20 == 0 or i == 0:
                    log.info(
                        f"[CAL] Captured "
                        f"{i + 1}/{CAPTURE_FRAMES} frames"
                    )

            except Exception as e:

                log.warning(
                    f"[CAL] Capture frame error: {e}"
                )

                continue

        if len(depth_stack) < 10:

            log.error(
                "[CAL] Insufficient frames captured"
            )

            return {
                "success": False,
                "reason": "INSUFFICIENT_FRAMES"
            }

        # ====================================================
        # BUILD STABLE MODEL
        # ====================================================

        log.info(
            "[CAL] Building stable depth model..."
        )

        stable_depth = np.median(
            np.stack(depth_stack),
            axis=0
        ).astype(np.float32)

        noise_map = np.std(
            np.stack(depth_stack),
            axis=0
        ).astype(np.float32)

        # ====================================================
        # SCALE REGION
        # ====================================================

        h, w = stable_depth.shape

        cx = w // 2
        cy = h // 2

        Y, X = np.ogrid[:h, :w]

        distance = np.sqrt(
            (X - cx) ** 2 +
            (Y - cy) ** 2
        )

        circle_mask = (
            distance <= INNER_RADIUS
        )

        # ====================================================
        # SCALE DEPTH
        # ====================================================

        scale_pixels = stable_depth[circle_mask]

        scale_pixels = scale_pixels[
            scale_pixels > 0
        ]

        scale_depth = np.median(scale_pixels)

        log.info(
            f"[CAL] Estimated scale depth: "
            f"{scale_depth:.1f} mm"
        )

        # ====================================================
        # SCALE PLANE
        # ====================================================

        scale_plane_mask = (
            np.abs(stable_depth - scale_depth)
            < PLANE_TOLERANCE_MM
        ).astype(np.uint8) * 255

        scale_plane_mask = cv2.morphologyEx(
            scale_plane_mask,
            cv2.MORPH_OPEN,
            np.ones((5, 5), np.uint8)
        )

        scale_plane_mask = cv2.morphologyEx(
            scale_plane_mask,
            cv2.MORPH_CLOSE,
            np.ones((7, 7), np.uint8)
        )

        # ====================================================
        # CONTOURS
        # ====================================================

        contours, _ = cv2.findContours(
            scale_plane_mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE
        )

        best_contour = None
        best_area = 0

        for cnt in contours:

            area = cv2.contourArea(cnt)

            if area < 10000:
                continue

            rect = cv2.minAreaRect(cnt)

            box = cv2.boxPoints(rect)
            box = np.int32(box)

            center = np.mean(box, axis=0)

            dist_to_center = np.linalg.norm(
                center - np.array([cx, cy])
            )

            if dist_to_center > 180:
                continue

            if area > best_area:

                best_area = area
                best_contour = cnt

        # ====================================================
        # FAIL SAFE
        # ====================================================

        if best_contour is None:

            log.error(
                "[CAL] Could not isolate weighing scale plane"
            )

            return {
                "success": False,
                "reason": "SCALE_PLANE_NOT_FOUND"
            }

        # ====================================================
        # FINAL SCALE RECT
        # ====================================================

        rect = cv2.minAreaRect(best_contour)

        box = cv2.boxPoints(rect)
        box = np.int32(box)

        scale_center = rect[0]

        # ====================================================
        # FLOOR DEPTH
        # ====================================================

        outside_mask = np.ones(
            stable_depth.shape,
            dtype=np.uint8
        ) * 255

        cv2.drawContours(
            outside_mask,
            [box],
            -1,
            0,
            -1
        )

        floor_pixels = stable_depth[
            outside_mask > 0
        ]

        floor_pixels = floor_pixels[
            floor_pixels > 0
        ]

        floor_depth = np.percentile(
            floor_pixels,
            50
        )

        scale_height = (
            floor_depth - scale_depth
        )

        # ====================================================
        # ROI
        # ====================================================

        scale_center_x = int(scale_center[0])
        scale_center_y = int(scale_center[1])

        roi_x1 = max(
            0,
            scale_center_x - ROI_HALF_SIZE
        )

        roi_y1 = max(
            0,
            scale_center_y - ROI_HALF_SIZE
        )

        roi_x2 = min(
            w - 1,
            scale_center_x + ROI_HALF_SIZE
        )

        roi_y2 = min(
            h - 1,
            scale_center_y + ROI_HALF_SIZE
        )

        # ====================================================
        # SAVE GEOMETRY
        # ====================================================

        geometry = {

            "floor_depth_mm":
            float(floor_depth),

            "scale_depth_mm":
            float(scale_depth),

            "scale_height_mm":
            float(scale_height),

            "scale_center_x":
            float(scale_center[0]),

            "scale_center_y":
            float(scale_center[1]),

            "roi_x1": int(roi_x1),
            "roi_y1": int(roi_y1),

            "roi_x2": int(roi_x2),
            "roi_y2": int(roi_y2)

        }

        np.save(
            os.path.join(
                SAVE_DIR,
                "stable_empty_depth.npy"
            ),
            stable_depth
        )

        np.save(
            os.path.join(
                SAVE_DIR,
                "noise_map.npy"
            ),
            noise_map
        )

        np.save(
            os.path.join(
                SAVE_DIR,
                "system_geometry.npy"
            ),
            geometry
        )

        # ====================================================
        # DIAGNOSTIC IMAGE
        # Saved to disk for HMI to retrieve and display.
        # No cv2.imshow — headless safe.
        # ====================================================

        if color_image is not None:

            result = color_image.copy()

            cv2.drawContours(
                result,
                [box],
                -1,
                (0, 255, 0),
                3
            )

            cv2.rectangle(
                result,
                (roi_x1, roi_y1),
                (roi_x2, roi_y2),
                (255, 255, 0),
                2
            )

            cv2.putText(
                result,
                "AUTO CALIBRATION SUCCESS",
                (60, 70),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.2,
                (0, 255, 0),
                3
            )

            diag_path = os.path.join(
                SAVE_DIR,
                "calibration_result.jpg"
            )

            cv2.imwrite(diag_path, result)

            log.info(
                f"[CAL] Diagnostic image saved: {diag_path}"
            )

        # ====================================================
        # SUCCESS
        # ====================================================

        log.info(
            "\n==================================="
            "\nAUTO CALIBRATION SUCCESS"
            "\n==================================="
            f"\nScale Depth : {scale_depth:.1f} mm"
            f"\nFloor Depth : {floor_depth:.1f} mm"
            f"\nScale Height: {scale_height:.1f} mm"
            "\nSaved:"
            "\n - stable_empty_depth.npy"
            "\n - noise_map.npy"
            "\n - system_geometry.npy"
            "\n - calibration_result.jpg"
        )

        return {
            "success": True
        }