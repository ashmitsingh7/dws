from ultralytics import YOLO
import cv2
import numpy as np
import logging

log = logging.getLogger("human_detector")


class HumanDetector:

    # ========================================================
    # INIT
    # ========================================================

    def __init__(

        self,

        model_name="yolov8s.pt",

        confidence=0.25,

        imgsz=640

    ):

        log.info(
            f"HumanDetector configured (lazy load): {model_name}"
        )

        self._model = None
        self._model_name = model_name

        self.confidence = confidence

        self.imgsz = imgsz

        self.last_boxes = []

    def _ensure_model(self):
        if self._model is None:
            log.info(f"Loading YOLO model: {self._model_name}")
            self._model = YOLO(self._model_name)

    # ========================================================
    # DETECT
    # ========================================================

    def detect(

        self,

        image,

        roi=None

    ):

        """
        Returns:
            bool → human detected

        Stores:
            self.last_boxes
        """

        self.last_boxes = []

        try:

            self._ensure_model()

            results = self._model(

                image,

                verbose=False,

                conf=self.confidence,

                imgsz=self.imgsz

            )

            detected = False

            for r in results:

                for det in r.boxes:

                    cls = int(det.cls[0])

                    # YOLO class 0 = person
                    if cls != 0:
                        continue

                    x1, y1, x2, y2 = map(
                        int,
                        det.xyxy[0]
                    )

                    # ====================================
                    # ROI INTERSECTION
                    # ====================================

                    if roi is not None:

                        roi_x1, roi_y1, roi_x2, roi_y2 = roi

                        ox1 = max(x1, roi_x1)
                        oy1 = max(y1, roi_y1)

                        ox2 = min(x2, roi_x2)
                        oy2 = min(y2, roi_y2)

                        if ox2 <= ox1 or oy2 <= oy1:
                            continue

                    # ====================================
                    # VALID HUMAN
                    # ====================================

                    detected = True

                    self.last_boxes.append(

                        (x1, y1, x2, y2)

                    )

            return detected

        except Exception as e:

            log.error(
                f"YOLO detection error: {e}"
            )

            return False

    # ========================================================
    # DRAW
    # ========================================================

    def draw(

        self,

        image

    ):

        for (

            x1,
            y1,
            x2,
            y2

        ) in self.last_boxes:

            cv2.rectangle(

                image,

                (x1, y1),

                (x2, y2),

                (0, 0, 255),

                2

            )

            cv2.putText(

                image,

                "HUMAN",

                (x1, y1 - 10),

                cv2.FONT_HERSHEY_SIMPLEX,

                0.7,

                (0, 0, 255),

                2

            )

        return image

    # ========================================================
    # GET BOXES
    # ========================================================

    def get_boxes(self):

        return self.last_boxes
