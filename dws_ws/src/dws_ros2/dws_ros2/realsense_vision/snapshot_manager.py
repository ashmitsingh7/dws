import os
import cv2
import uuid
import logging

from datetime import datetime

from dws_ros2.paths import CAPTURE_DIR as _DEFAULT_CAPTURE_DIR


log = logging.getLogger("snapshot_manager")


class SnapshotManager:

    # ========================================================
    # INIT
    # ========================================================

    def __init__(

        self,

        base_dir=_DEFAULT_CAPTURE_DIR

    ):

        self.base_dir = base_dir

        os.makedirs(
            self.base_dir,
            exist_ok=True
        )

    # ========================================================
    # DIRECTORY STRUCTURE
    # ========================================================

    def build_directory(self):

        now = datetime.now()

        year  = now.strftime("%Y")
        month = now.strftime("%m")
        day   = now.strftime("%d")

        path = os.path.join(

            self.base_dir,

            year,
            month,
            day

        )

        os.makedirs(
            path,
            exist_ok=True
        )

        return path

    # ========================================================
    # FILENAME
    # ========================================================

    def generate_filename(

        self,

        prefix="parcel"

    ):

        timestamp = datetime.now().strftime(
            "%H%M%S"
        )

        uid = str(uuid.uuid4())[:8]

        filename = (
            f"{prefix}_{timestamp}_{uid}.jpg"
        )

        return filename

    # ========================================================
    # SAVE SNAPSHOT
    # ========================================================

    def save(

        self,

        image,

        dimensions=None,

        parcel_id=None,

        confidence=None,

        tx_id=None,

        weight_kg=None

    ):

        """
        Saves annotated parcel image.

        Returns:
            dict
        """

        directory = self.build_directory()

        filename = self.generate_filename()

        filepath = os.path.join(
            directory,
            filename
        )

        # ====================================================
        # OVERLAY METADATA
        # ====================================================

        annotated = image.copy()

        self.draw_metadata(

            annotated,

            dimensions,

            weight_kg

        )

        # ====================================================
        # SAVE
        # ====================================================

        log.info(f"[capture] Saving snapshot to: {filepath}")

        success = cv2.imwrite(
            filepath,
            annotated
        )

        if not success:

            log.error(
                f"[capture] Failed to save snapshot: {filepath}"
            )

            return None

        log.info(
            f"[capture] Snapshot saved OK: {filepath}"
        )

        return {

            "path": filepath,

            "filename": filename,

            "timestamp": datetime.now().isoformat()

        }

    # ========================================================
    # METADATA OVERLAY — clean vMeasure-style
    # ========================================================

    def draw_metadata(

        self,

        image,

        dimensions=None,

        weight_kg=None

    ):

        """
        Draws a compact measurement overlay in the top-left corner.
        Shows only: timestamp, L, B, H, Wt.
        No confidence, no parcel ID, no large background panel.
        """

        # ====================================================
        # COLLECT VALUES
        # ====================================================

        timestamp = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        if dimensions is not None:
            L_cm = dimensions.get("length_mm", 0) / 10.0
            B_cm = dimensions.get("breadth_mm", 0) / 10.0
            H_cm = dimensions.get("height_mm", 0) / 10.0
        else:
            L_cm = B_cm = H_cm = 0.0

        lines = [
            timestamp,
            "",
            f"L  : {L_cm:.1f} cm",
            f"B  : {B_cm:.1f} cm",
            f"H  : {H_cm:.1f} cm",
        ]

        if weight_kg is not None:
            lines.append(f"Wt : {weight_kg:.2f} kg")

        # ====================================================
        # MEASURE TEXT BLOCK SIZE
        # ====================================================

        font       = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.6
        thickness  = 1
        line_h     = 22
        pad_x      = 10
        pad_y      = 8

        max_w = 0
        for ln in lines:
            if not ln:
                continue
            (tw, _), _ = cv2.getTextSize(
                ln, font, font_scale, thickness
            )
            max_w = max(max_w, tw)

        block_w = max_w + pad_x * 2
        block_h = len(lines) * line_h + pad_y * 2

        # ====================================================
        # SEMI-TRANSPARENT BACKGROUND
        # ====================================================

        x0, y0 = 12, 12
        x1, y1 = x0 + block_w, y0 + block_h

        overlay = image.copy()

        cv2.rectangle(
            overlay,
            (x0, y0),
            (x1, y1),
            (0, 0, 0),
            -1
        )

        cv2.addWeighted(
            overlay, 0.55,
            image,  0.45,
            0,
            image
        )

        # ====================================================
        # DRAW TEXT
        # ====================================================

        cur_y = y0 + pad_y + line_h - 6

        for i, ln in enumerate(lines):

            if not ln:
                cur_y += line_h
                continue

            color = (
                (180, 180, 180)
                if i == 0
                else (255, 255, 255)
            )

            cv2.putText(

                image,

                ln,

                (x0 + pad_x, cur_y),

                font,

                font_scale,

                color,

                thickness,

                cv2.LINE_AA

            )

            cur_y += line_h
