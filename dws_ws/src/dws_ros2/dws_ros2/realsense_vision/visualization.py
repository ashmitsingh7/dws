import cv2
import numpy as np


class VisualizationManager:

    # ========================================================
    # INIT
    # ========================================================

    def __init__(self):

        pass

    # ========================================================
    # MAIN RENDER
    # ========================================================

    def render(

        self,

        color_image,

        result,

        calibration,

        human_detector=None

    ):

        """
        Returns:
            annotated_image
        """

        frame = color_image.copy()

        # ====================================================
        # CALIBRATION OVERLAY
        # ====================================================

        workspace_pts = (
            calibration.get_workspace_polygon()
        )

        scale_center = (
            calibration.get_scale_center()
        )

        cv2.polylines(

            frame,

            [workspace_pts],

            True,

            (0, 200, 255),

            2

        )

        cv2.circle(

            frame,

            scale_center,

            60,

            (0, 255, 0),

            2

        )

        cv2.circle(

            frame,

            scale_center,

            5,

            (0, 255, 0),

            -1

        )

        cv2.putText(

            frame,

            "PLACE PARCEL HERE",

            (

                scale_center[0] - 100,
                scale_center[1] - 75

            ),

            cv2.FONT_HERSHEY_SIMPLEX,

            0.55,

            (0,255,0),

            2

        )

        # ====================================================
        # HUMAN BOXES
        # ====================================================

        if human_detector:

            frame = human_detector.draw(
                frame
            )

        # ====================================================
        # INVALID STATES
        # ====================================================

        if not result["valid"]:

            reason = result.get(
                "reason",
                "INVALID"
            )

            self.draw_error(
                frame,
                reason
            )

            return frame

        # ====================================================
        # BOX
        # ====================================================

        box = result["box"]

        cv2.drawContours(

            frame,

            [box],

            -1,

            (0,255,0),

            2

        )

        # ====================================================
        # LOCK STATE
        # ====================================================

        if result["locked"]:

            cv2.putText(

                frame,

                "LOCKED",

                (

                    frame.shape[1] - 160,
                    45

                ),

                cv2.FONT_HERSHEY_SIMPLEX,

                1,

                (0,255,0),

                2

            )

        else:

            cv2.putText(

                frame,

                "STABILIZING",

                (

                    frame.shape[1] - 220,
                    45

                ),

                cv2.FONT_HERSHEY_SIMPLEX,

                1,

                (0,255,255),

                2

            )

        # ====================================================
        # DIMENSION PANEL
        # ====================================================

        self.draw_dimension_panel(
            frame,
            result
        )

        # ====================================================
        # CONFIDENCE
        # ====================================================

        confidence = result.get(
            "confidence",
            0
        )

        cv2.putText(

            frame,

            f"CONF: {confidence:.2f}",

            (

                18,
                frame.shape[0] - 15

            ),

            cv2.FONT_HERSHEY_SIMPLEX,

            0.5,

            (180,180,180),

            1

        )

        return frame

    # ========================================================
    # DIMENSION PANEL
    # ========================================================

    def draw_dimension_panel(

        self,

        frame,

        result

    ):

        L = result["length_mm"]
        B = result["breadth_mm"]
        H = result["height_mm"]

        # ====================================================
        # BACKGROUND PANEL
        # ====================================================

        cv2.rectangle(

            frame,

            (10, 48),

            (330, 230),

            (0,0,0),

            -1

        )

        # ====================================================
        # TEXT
        # ====================================================

        cv2.putText(

            frame,

            f"L: {L:>7.1f} mm",

            (18, 90),

            cv2.FONT_HERSHEY_SIMPLEX,

            1.0,

            (255,255,255),

            2

        )

        cv2.putText(

            frame,

            f"B: {B:>7.1f} mm",

            (18, 150),

            cv2.FONT_HERSHEY_SIMPLEX,

            1.0,

            (255,255,255),

            2

        )

        cv2.putText(

            frame,

            f"H: {H:>7.1f} mm",

            (18, 210),

            cv2.FONT_HERSHEY_SIMPLEX,

            1.0,

            (255,255,255),

            2

        )

    # ========================================================
    # ERROR PANEL
    # ========================================================

    def draw_error(

        self,

        frame,

        reason

    ):

        # ====================================================
        # MAP REASONS
        # ====================================================

        mapping = {

            "NO_OBJECT":
                "NO PARCEL DETECTED",

            "MULTIPLE_OBJECTS":
                "MULTIPLE OBJECTS",

            "HUMAN_INTERFERENCE":
                "HUMAN INTERFERENCE",

            "DIMENSION_FAILURE":
                "MEASUREMENT FAILURE"

        }

        text = mapping.get(
            reason,
            reason
        )

        # ====================================================
        # PANEL
        # ====================================================

        cv2.rectangle(

            frame,

            (10, 10),

            (500, 80),

            (0,0,0),

            -1

        )

        cv2.putText(

            frame,

            text,

            (20, 55),

            cv2.FONT_HERSHEY_SIMPLEX,

            1,

            (0,0,255),

            2

        )

    # ========================================================
    # MASK VISUALIZATION
    # ========================================================

    def render_mask(

        self,

        mask

    ):

        if len(mask.shape) == 2:

            return cv2.cvtColor(
                mask,
                cv2.COLOR_GRAY2BGR
            )

        return mask
