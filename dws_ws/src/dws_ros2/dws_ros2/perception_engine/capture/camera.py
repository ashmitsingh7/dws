"""
capture/camera.py

Stage 1 — Capture Engine.

Thin, correctness-first wrapper around pyrealsense2. Mirrors the same
align/intrinsics pattern used in auto_calibration_v2.py so depth and
color frames are always pixel-aligned and consistent with how the
calibration files (stable_empty_depth.npy etc.) were generated.

This module requires a physically connected RealSense device and the
pyrealsense2 package. It is intentionally NOT unit-tested in this
environment — review for correctness against the librealsense API,
verify on real hardware.
"""

from __future__ import annotations
import time
import numpy as np

from .frame import Frame, Intrinsics

try:
    import pyrealsense2 as rs
except ImportError:  # pragma: no cover
    rs = None


class RealSenseCamera:
    """
    Manages a single RealSense pipeline (color + depth, aligned to color),
    matching the stream config used during calibration:
        depth: 1280x720 @ 30fps, z16
        color: 1280x720 @ 30fps, bgr8
    """

    def __init__(
        self,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        warmup_frames: int = 30,
    ):
        if rs is None:
            raise RuntimeError(
                "pyrealsense2 is not installed. Install it on the machine "
                "with the D455 attached (pip install pyrealsense2)."
            )

        self.width = width
        self.height = height
        self.fps = fps
        self.warmup_frames = warmup_frames

        self._pipeline = rs.pipeline()
        self._config = rs.config()
        self._config.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)
        self._config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)

        self._profile = None
        self._align = None
        self._depth_scale = None
        self._intrinsics: Intrinsics | None = None
        self._started = False

    # ------------------------------------------------------------------
    def start(self):
        """Start the pipeline, set up alignment, read intrinsics, warm up."""
        self._profile = self._pipeline.start(self._config)
        self._align = rs.align(rs.stream.color)

        depth_sensor = self._profile.get_device().first_depth_sensor()
        self._depth_scale = depth_sensor.get_depth_scale()

        # Intrinsics are read from the color stream profile since we align
        # depth -> color, so post-alignment depth pixels share color's
        # intrinsics.
        color_stream = self._profile.get_stream(rs.stream.color)
        intr = color_stream.as_video_stream_profile().get_intrinsics()
        self._intrinsics = Intrinsics(
            width=intr.width,
            height=intr.height,
            fx=intr.fx,
            fy=intr.fy,
            ppx=intr.ppx,
            ppy=intr.ppy,
            depth_scale=self._depth_scale,
        )

        # Warmup: let auto-exposure / depth pipeline settle before any
        # frame is trusted, matching the pattern in auto_calibration_v2.py.
        for _ in range(self.warmup_frames):
            try:
                frames = self._pipeline.wait_for_frames()
                self._align.process(frames)
            except Exception:
                continue

        self._started = True
        return self

    # ------------------------------------------------------------------
    def read(self) -> Frame | None:
        """
        Capture and return a single aligned Frame, or None if a frame
        couldn't be acquired (caller should retry).
        """
        if not self._started:
            raise RuntimeError("Camera not started. Call start() first.")

        try:
            frames = self._pipeline.wait_for_frames()
            aligned = self._align.process(frames)

            depth_frame = aligned.get_depth_frame()
            color_frame = aligned.get_color_frame()

            if not depth_frame or not color_frame:
                return None

            color_image = np.asanyarray(color_frame.get_data())
            depth_raw = np.asanyarray(depth_frame.get_data())  # uint16
            depth_m = depth_raw.astype(np.float32) * self._depth_scale

            return Frame(
                color=color_image,
                depth_raw=depth_raw,
                depth_m=depth_m,
                intrinsics=self._intrinsics,
                timestamp=time.monotonic(),
            )
        except Exception:
            return None

    # ------------------------------------------------------------------
    def stop(self):
        if self._started:
            try:
                self._pipeline.stop()
            except Exception:
                pass
            self._started = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
