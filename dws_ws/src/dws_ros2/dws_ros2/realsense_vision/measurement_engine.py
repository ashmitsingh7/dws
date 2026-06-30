import numpy as np
import cv2

from collections import deque


class MeasurementEngine:

    # ========================================================
    # INIT
    # ========================================================

    def __init__(

        self,

        calibration,

        human_detector=None

    ):

        self.calibration = calibration
        self.human_detector = human_detector

        # ====================================================
        # CONFIG
        # ====================================================

        self.DEPTH_THRESHOLD_MIN = 40
        self.DEPTH_THRESHOLD_MAX = 1000

        # increased to suppress tiny blobs
        self.MIN_CONTOUR_AREA = 4000

        # stricter large contour rejection
        self.MAX_CONTOUR_AREA_RATIO = 0.25

        self.CENTER_WEIGHT = 2.5
        self.SOLIDITY_THRESHOLD = 0.78

        self.DEPTH_PATCH_HALF = 12

        self.TRIM_L = 1.0
        self.TRIM_B = 1.0
        self.TRIM_H = 0.971

        self.HEIGHT_PERCENTILE = 55

        self.LOCK_FRAMES = 20
        self.MAX_DIMENSION_DRIFT_MM = 15

        # HUMAN DETECTOR TEMP DISABLED
        self.HUMAN_OVERLAP_PX = 1500

        # relaxed
        self.MAX_DEPTH_STD = 180

        # ====================================================
        # STABILIZATION
        # ====================================================

        self.L_buffer = deque(maxlen=self.LOCK_FRAMES)
        self.B_buffer = deque(maxlen=self.LOCK_FRAMES)
        self.H_buffer = deque(maxlen=self.LOCK_FRAMES)

        self.locked = False

        self.locked_L = 0.0
        self.locked_B = 0.0
        self.locked_H = 0.0

        # ====================================================
        # CALIBRATION
        # ====================================================

        self.empty_depth = calibration.empty_depth

        self.workspace_pts = (
            calibration.get_workspace_polygon()
        )

        (
            self.roi_x1,
            self.roi_y1,
            self.roi_x2,
            self.roi_y2

        ) = calibration.get_roi()

        self.scale_center_x = (
            calibration.scale_center_x
        )

        self.scale_center_y = (
            calibration.scale_center_y
        )

        self.fx = calibration.fx
        self.fy = calibration.fy

        self.ppx = calibration.ppx
        self.ppy = calibration.ppy

    # ========================================================
    # HELPERS
    # ========================================================

    def reset_lock(self):

        # =====================================
        # Reset lock state
        # =====================================

        self.locked = False

        self.lock_counter = 0

        self.stable_counter = 0

        # =====================================
        # Reset dimension history
        # =====================================

        self.last_dimensions = None

        self.last_valid_result = None

        # =====================================
        # Clear rolling buffers
        # =====================================

        self.L_buffer.clear()

        self.B_buffer.clear()

        self.H_buffer.clear()

        # =====================================
        # Clear tracking history
        # =====================================

        if hasattr(self, "confidence_history"):
            self.confidence_history.clear()

        if hasattr(self, "depth_history"):
            self.depth_history.clear()

        if hasattr(self, "contour_history"):
            self.contour_history.clear()

        # =====================================
        # Reset contour/object state
        # =====================================

        self.current_contour = None

        self.current_mask = None

        self.current_bbox = None

        print(
            "[MeasurementEngine] "
            "Measurement state reset"
        )

    # ========================================================

    def depth_diff_safe(

        self,

        empty,

        current

    ):

        e = empty.astype(np.float32)
        c = current.astype(np.float32)

        diff = e - c

        diff[diff < 0] = 0

        return diff

    # ========================================================

    def ransac_height(

        self,

        delta,

        n_iter=50,

        tol=5.0,

        min_inliers=60

    ):

        if len(delta) < min_inliers:
            return None

        rng = np.random.default_rng(0)

        best_n = 0
        best_median = None

        for _ in range(n_iter):

            seed = rng.choice(delta)

            inliers = delta[
                np.abs(delta - seed) < tol
            ]

            if len(inliers) > best_n:

                best_n = len(inliers)

                best_median = float(
                    np.median(inliers)
                )

        if best_n >= min_inliers:
            return best_median

        return None

    # ========================================================

    def pixels_to_mm(

        self,

        width_px,

        height_px,

        depth_mm,

        rect_cx,

        rect_cy

    ):

        cos_x = 1.0 / np.sqrt(
            1.0 + (
                (
                    rect_cx - self.ppx
                ) / self.fx
            ) ** 2
        )

        cos_y = 1.0 / np.sqrt(
            1.0 + (
                (
                    rect_cy - self.ppy
                ) / self.fy
            ) ** 2
        )

        dim_x = (
            width_px * depth_mm / self.fx
        ) * cos_x

        dim_y = (
            height_px * depth_mm / self.fy
        ) * cos_y

        return dim_x, dim_y

    # ========================================================
    # MAIN PROCESS
    # ========================================================

    def process(

        self,

        color_image,

        depth_image

    ):

        rgb = color_image.copy()

        frame_area = (
            rgb.shape[0] * rgb.shape[1]
        )

        # ====================================================
        # HUMAN DETECTION DISABLED
        # ====================================================

        human_detected = False

        # ====================================================
        # DEPTH DIFFERENCE
        # ====================================================

        diff = self.depth_diff_safe(

            self.empty_depth,

            depth_image

        )

        mask = np.where(

            (
                diff > self.DEPTH_THRESHOLD_MIN
            )

            &

            (
                diff < self.DEPTH_THRESHOLD_MAX
            ),

            255,

            0

        ).astype(np.uint8)

        # ====================================================
        # WORKSPACE MASK
        # ====================================================

        ws_mask = np.zeros_like(mask)

        cv2.fillPoly(

            ws_mask,

            [self.workspace_pts],

            255

        )

        mask = cv2.bitwise_and(

            mask,

            ws_mask

        )

        # ====================================================
        # MORPHOLOGY
        # ====================================================

        k3 = np.ones((3, 3), np.uint8)
        k5 = np.ones((5, 5), np.uint8)

        mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_OPEN,
            k3,
            iterations=1
        )

        mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_CLOSE,
            k5,
            iterations=1
        )

        mask = cv2.erode(
            mask,
            k3,
            iterations=1
        )

        # ====================================================
        # REMOVE SMALL BLOBS
        # ====================================================

        num_labels, labels, stats, _ = (

            cv2.connectedComponentsWithStats(

                mask,

                connectivity=8

            )

        )

        clean_mask = np.zeros_like(mask)

        for i in range(1, num_labels):

            area = stats[i, cv2.CC_STAT_AREA]

            if area < 2500:
                continue

            clean_mask[labels == i] = 255

        mask = clean_mask

        # ====================================================
        # CONTOURS
        # ====================================================

        contours, _ = cv2.findContours(

            mask,

            cv2.RETR_EXTERNAL,

            cv2.CHAIN_APPROX_SIMPLE

        )

        valid_contours = []

        for c in contours:

            area = cv2.contourArea(c)

            if (

                self.MIN_CONTOUR_AREA

                <= area <=

                frame_area
                *
                self.MAX_CONTOUR_AREA_RATIO

            ):

                valid_contours.append(c)

        # ====================================================
        # MULTIPLE OBJECTS
        # ====================================================

        if len(valid_contours) > 1:

            self.reset_lock()

            return {

                "valid": False,

                "locked": False,

                "reason": "MULTIPLE_OBJECTS",

                "object_count": len(valid_contours),

                "contours": valid_contours,

                "mask": mask

            }

        # ====================================================
        # NO PARCEL
        # ====================================================

        if len(valid_contours) == 0:

            self.reset_lock()

            return {

                "valid": False,

                "locked": False,

                "reason": "NO_PARCEL",

                "mask": mask

            }

        contour = valid_contours[0]

        # ====================================================
        # EDGE TOUCH REJECTION
        # ====================================================

        x, y, w, h = cv2.boundingRect(contour)

        margin = 8

        if (

            x <= self.roi_x1 + margin

            or

            y <= self.roi_y1 + margin

            or

            (x + w) >= self.roi_x2 - margin

            or

            (y + h) >= self.roi_y2 - margin

        ):

            self.reset_lock()

            return {

                "valid": False,

                "locked": False,

                "reason": "EDGE_TOUCH",

                "mask": mask

            }

        # ====================================================
        # RECTANGLE
        # ====================================================

        epsilon = (
            0.02 *
            cv2.arcLength(contour, True)
        )

        approx = cv2.approxPolyDP(

            contour,

            epsilon,

            True

        )

        rect = cv2.minAreaRect(approx)

        box = np.int32(
            cv2.boxPoints(rect)
        )

        rect_cx = rect[0][0]
        rect_cy = rect[0][1]

        width_px = rect[1][0]
        height_px = rect[1][1]

        # ====================================================
        # OBJECT DEPTH
        # ====================================================

        cx_i = int(rect_cx)
        cy_i = int(rect_cy)

        y0 = max(
            0,
            cy_i - self.DEPTH_PATCH_HALF
        )

        y1 = min(
            depth_image.shape[0],
            cy_i + self.DEPTH_PATCH_HALF
        )

        x0 = max(
            0,
            cx_i - self.DEPTH_PATCH_HALF
        )

        x1 = min(
            depth_image.shape[1],
            cx_i + self.DEPTH_PATCH_HALF
        )

        patch = depth_image[
            y0:y1,
            x0:x1
        ]

        valid_d = patch[patch > 0]

        if len(valid_d) < 8:

            return {

                "valid": False,

                "locked": False,

                "reason": "BAD_DEPTH",

                "mask": mask

            }

        p10, p90 = np.percentile(
            valid_d,
            [10, 90]
        )

        trimmed = valid_d[
            (
                valid_d >= p10
            )
            &
            (
                valid_d <= p90
            )
        ]

        object_depth_mm = float(
            np.mean(trimmed)
        )

        # ====================================================
        # DIMENSIONS
        # ====================================================

        dim_x_mm, dim_y_mm = (

            self.pixels_to_mm(

                width_px,

                height_px,

                object_depth_mm,

                rect_cx,

                rect_cy

            )

        )

        raw_length_mm = max(
            dim_x_mm,
            dim_y_mm
        )

        raw_breadth_mm = min(
            dim_x_mm,
            dim_y_mm
        )

        length_mm = (
            raw_length_mm * self.TRIM_L
        )

        breadth_mm = (
            raw_breadth_mm * self.TRIM_B
        )

        # ====================================================
        # HEIGHT
        # ====================================================

        object_region = mask > 0

        empty_vals = self.empty_depth[
            object_region
        ].astype(np.float32)

        current_vals = depth_image[
            object_region
        ].astype(np.float32)

        valid_h = (
            (empty_vals > 0)
            &
            (current_vals > 0)
        )

        if np.sum(valid_h) < 100:

            return {

                "valid": False,

                "locked": False,

                "reason": "BAD_HEIGHT",

                "mask": mask

            }

        delta = (
            empty_vals[valid_h]
            -
            current_vals[valid_h]
        )

        delta = delta[
            delta > 0
        ]

        if len(delta) < 50:

            return {

                "valid": False,

                "locked": False,

                "reason": "BAD_HEIGHT",

                "mask": mask

            }

        lo, hi = np.percentile(
            delta,
            [10, 90]
        )

        delta = delta[
            (
                delta >= lo
            )
            &
            (
                delta <= hi
            )
        ]

        depth_std = float(
            np.std(delta)
        )

        height_raw = self.ransac_height(

            delta,

            tol=5.0,

            min_inliers=50

        )

        if height_raw is None:

            height_raw = float(

                np.percentile(

                    delta,

                    self.HEIGHT_PERCENTILE

                )

            )

        height_mm = (
            height_raw * self.TRIM_H
        )

        # ====================================================
        # DEPTH NOISE CHECK ONLY
        # ====================================================

        if depth_std > self.MAX_DEPTH_STD:

            self.reset_lock()

            return {

                "valid": False,

                "locked": False,

                "reason": "DEPTH_NOISE",

                "mask": mask,

                "box": box

            }

        # ====================================================
        # SANITY GATE
        # ====================================================

        if not (

            40 < length_mm < 3000

            and

            40 < breadth_mm < 3000

            and

            10 < height_mm < 2000

        ):

            return {

                "valid": False,

                "locked": False,

                "reason": "SANITY_FAIL",

                "mask": mask

            }

        # ====================================================
        # STABILIZATION
        # ====================================================

        if not self.locked:

            self.L_buffer.append(length_mm)
            self.B_buffer.append(breadth_mm)
            self.H_buffer.append(height_mm)

        live_L = float(
            np.median(self.L_buffer)
        )

        live_B = float(
            np.median(self.B_buffer)
        )

        live_H = float(
            np.median(self.H_buffer)
        )

        if (

            len(self.L_buffer)
            >=
            self.LOCK_FRAMES

            and

            not self.locked

        ):

            self.locked = True

            self.locked_L = live_L
            self.locked_B = live_B
            self.locked_H = live_H

            print(
                f"LOCKED | "
                f"L={live_L:.1f} "
                f"B={live_B:.1f} "
                f"H={live_H:.1f}"
            )

        # ====================================================
        # DRIFT CHECK
        # ====================================================

        if self.locked:

            if (

                abs(
                    live_L - self.locked_L
                )

                >

                self.MAX_DIMENSION_DRIFT_MM

                or

                abs(
                    live_B - self.locked_B
                )

                >

                self.MAX_DIMENSION_DRIFT_MM

                or

                abs(
                    live_H - self.locked_H
                )

                >

                self.MAX_DIMENSION_DRIFT_MM

            ):

                self.reset_lock()

        # ====================================================
        # FINAL VALUES
        # ====================================================

        dL = (
            self.locked_L
            if self.locked
            else live_L
        )

        dB = (
            self.locked_B
            if self.locked
            else live_B
        )

        dH = (
            self.locked_H
            if self.locked
            else live_H
        )

        confidence = min(

            1.0,

            len(self.L_buffer)
            /
            self.LOCK_FRAMES

        )

        # ====================================================
        # RETURN
        # ====================================================

        return {

            "valid": True,

            "locked": self.locked,

            "stable": self.locked,

            "length_mm": float(dL),

            "breadth_mm": float(dB),

            "height_mm": float(dH),

            "confidence": float(confidence),

            "human_detected": human_detected,

            "depth_std": depth_std,

            "object_depth_mm": object_depth_mm,

            "mask": mask,

            "box": box,

            "contour": contour,

            "reason": None

        }