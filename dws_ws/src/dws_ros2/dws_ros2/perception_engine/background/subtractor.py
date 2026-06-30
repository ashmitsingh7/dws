"""
background/subtractor.py

Stage 2 — Background Subtraction.

current_depth - stable_empty_depth -> foreground mask, using the
calibration's stable_empty_depth.npy instead of a naive fixed threshold.
"""

from __future__ import annotations
import numpy as np
import cv2


class BackgroundSubtractor:
    def __init__(self, stable_empty_depth_m: np.ndarray, min_height_m: float = 0.01):
        """
        stable_empty_depth_m: (H, W) float32, meters, from calibration.
        min_height_m: minimum depth difference from the empty scene to
            count as foreground (filters sensor jitter at the floor level).
        """
        self.stable_empty_depth_m = stable_empty_depth_m
        self.min_height_m = min_height_m

    def subtract(self, depth_m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        Returns (foreground_mask, height_above_empty_m).
        foreground_mask: (H, W) uint8, 255 where an object is present.
        height_above_empty_m: (H, W) float32, positive where something is
            closer to the camera than the empty scene (i.e. an object is
            present), 0 elsewhere.
        """
        valid = depth_m > 0
        empty = self.stable_empty_depth_m

        # Object is *closer* to camera than empty scene -> smaller depth.
        diff = np.where(valid, empty - depth_m, 0.0).astype(np.float32)
        diff = np.clip(diff, 0.0, None)

        mask = ((diff > self.min_height_m) & valid).astype(np.uint8) * 255

        # Light morphology to clean sensor speckle, matching the pattern
        # used in auto_calibration_v2.py's scale-plane mask cleanup.
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

        return mask, diff
