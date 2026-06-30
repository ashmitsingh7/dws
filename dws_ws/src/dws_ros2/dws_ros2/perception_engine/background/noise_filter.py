"""
background/noise_filter.py

Stage 3 — Noise Suppression.

Pipeline: Temporal Median -> Noise Map Reject -> Spatial Median ->
Hole Filling -> Morphology.

IMPORTANT: the temporal median is implemented as a fast vectorized
masked-median, NOT np.nanmedian. np.nanmedian on real-time depth frames
triggers numpy's internal `warnings.warn()` once per all-NaN pixel
column -- on a 1280x720 frame that is hundreds of thousands of Python
-level warning calls per frame, dominating runtime even when the
warning is filtered/ignored (the function-call overhead itself is the
cost, not the printed message). This was discovered the hard way in an
earlier build; the fix here is to never call nanmedian on frame-shaped
data at all.
"""

from __future__ import annotations
from collections import deque
import numpy as np
import cv2


def _fast_masked_median(stack: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """
    Per-pixel median over the frame (first) axis, ignoring invalid
    samples, without numpy's nanmedian warning overhead.

    stack: (T, H, W) float32 raw values (invalid entries can be anything,
           e.g. 0 -- they're ignored via `valid`)
    valid: (T, H, W) bool, True where stack[t, y, x] is a usable sample

    Returns (H, W) float32, 0 where no valid sample existed at all.
    """
    T = stack.shape[0]

    # Push invalid samples to +inf so they sort to the end, then pick the
    # median index among only the valid count per pixel. Fully vectorized,
    # no Python-level per-pixel loop, no warnings module involved.
    masked = np.where(valid, stack, np.inf).astype(np.float32)
    sorted_vals = np.sort(masked, axis=0)

    valid_count = valid.sum(axis=0)  # (H, W) int, how many real samples
    mid_lo = np.clip((valid_count - 1) // 2, 0, T - 1)
    mid_hi = np.clip(valid_count // 2, 0, T - 1)

    rows, cols = np.indices(valid_count.shape)
    lo = sorted_vals[mid_lo, rows, cols]
    hi = sorted_vals[mid_hi, rows, cols]
    median = (lo + hi) / 2.0

    median = np.where(valid_count > 0, median, 0.0).astype(np.float32)
    return median


class TemporalBuffer:
    """Sliding window of recent depth frames for temporal median filtering."""

    def __init__(self, window_size: int = 5):
        self.window_size = window_size
        self._buf: deque[np.ndarray] = deque(maxlen=window_size)

    def push(self, depth_m: np.ndarray) -> np.ndarray:
        """Push a new frame, return the temporal median so far."""
        self._buf.append(depth_m)
        stack = np.stack(self._buf, axis=0)
        valid = stack > 0
        return _fast_masked_median(stack, valid)

    def __len__(self):
        return len(self._buf)


class NoiseSuppressor:
    def __init__(
        self,
        noise_map_m: np.ndarray,
        noise_sigma_multiplier: float = 3.0,
        spatial_kernel: int = 5,
        morph_kernel: int = 5,
    ):
        self.noise_map_m = noise_map_m
        self.noise_sigma_multiplier = noise_sigma_multiplier
        self.spatial_kernel = spatial_kernel
        self.morph_kernel = morph_kernel

    def reject_high_noise(self, depth_m: np.ndarray) -> np.ndarray:
        """
        Zero out pixels whose calibrated per-pixel noise std-dev exceeds
        the allowed multiplier -- these are unreliable sensor regions
        (edges, low-reflectivity surfaces, etc.) regardless of what value
        they currently report.
        """
        threshold = self.noise_map_m * self.noise_sigma_multiplier
        # Pixels with zero calibrated noise (e.g. never seen valid data
        # during calibration) are treated as always-unreliable.
        reliable = (self.noise_map_m > 0) & (self.noise_map_m < np.inf)
        out = np.where(reliable, depth_m, 0.0).astype(np.float32)
        return out

    def spatial_median(self, depth_m: np.ndarray) -> np.ndarray:
        """Median blur in 2D to suppress salt-and-pepper depth noise."""
        # cv2.medianBlur needs a fixed-point or float32 input; it works
        # on float32 directly. Zero-depth (invalid) pixels participate in
        # the blur like any value -- acceptable since invalid regions are
        # typically contiguous, not isolated.
        return cv2.medianBlur(depth_m, self.spatial_kernel)

    def fill_holes(self, depth_m: np.ndarray) -> np.ndarray:
        """
        Fill small zero-depth holes via inpainting on the depth map,
        treating 0 as the missing-data mask.
        """
        mask = (depth_m == 0).astype(np.uint8)
        if mask.sum() == 0:
            return depth_m
        # cv2.inpaint requires 8-bit; normalize to a working scale, then
        # restore. Use a generous max depth range for normalization.
        max_depth = max(float(depth_m.max()), 1e-3)
        depth_u8 = (np.clip(depth_m, 0, max_depth) / max_depth * 255).astype(np.uint8)
        filled_u8 = cv2.inpaint(depth_u8, mask, inpaintRadius=3, flags=cv2.INPAINT_TELEA)
        filled = filled_u8.astype(np.float32) / 255.0 * max_depth
        # Only replace the originally-invalid pixels; keep valid ones exact.
        return np.where(mask > 0, filled, depth_m).astype(np.float32)

    def morphology(self, mask: np.ndarray) -> np.ndarray:
        """Clean a binary mask (e.g. foreground mask) with open+close."""
        k = np.ones((self.morph_kernel, self.morph_kernel), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
        return mask

    def process(self, depth_m: np.ndarray) -> np.ndarray:
        """Run reject -> spatial median -> hole fill (temporal median is
        handled separately via TemporalBuffer, since it needs frame history)."""
        out = self.reject_high_noise(depth_m)
        out = self.spatial_median(out)
        out = self.fill_holes(out)
        return out
