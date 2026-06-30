import pyrealsense2 as rs
import numpy as np
import time
import logging

log = logging.getLogger("realsense_camera")


class RealSenseCamera:

    def __init__(

        self,

        width=1280,
        height=720,
        fps=30

    ):

        self.width = width
        self.height = height
        self.fps = fps

        self.pipeline = None
        self.config = None
        self.profile = None

        self.align = None

        self.spatial = None
        self.temporal = None
        self.hole_filling = None

        self.running = False

        self.fx = None
        self.fy = None

        self.ppx = None
        self.ppy = None

        self.depth_scale = None

        self.last_frame_time = 0

    # ========================================================
    # START CAMERA
    # ========================================================

    def start(self):

        log.info("Starting RealSense pipeline...")

        self.pipeline = rs.pipeline()

        self.config = rs.config()

        self.config.enable_stream(
            rs.stream.depth,
            self.width,
            self.height,
            rs.format.z16,
            self.fps
        )

        self.config.enable_stream(
            rs.stream.color,
            self.width,
            self.height,
            rs.format.bgr8,
            self.fps
        )

        self.profile = self.pipeline.start(
            self.config
        )

        self.align = rs.align(
            rs.stream.color
        )

        # ====================================================
        # FILTERS
        # ====================================================

        self.spatial = rs.spatial_filter()

        self.temporal = rs.temporal_filter()

        self.hole_filling = rs.hole_filling_filter()

        # ====================================================
        # INTRINSICS
        # ====================================================

        intrinsics = (

            self.profile
            .get_stream(rs.stream.color)
            .as_video_stream_profile()
            .get_intrinsics()

        )

        self.fx = intrinsics.fx
        self.fy = intrinsics.fy

        self.ppx = intrinsics.ppx
        self.ppy = intrinsics.ppy

        self.depth_scale = (
            self.profile
            .get_device()
            .first_depth_sensor()
            .get_depth_scale()
        )

        log.info(
            f"Camera intrinsics | "
            f"fx={self.fx:.2f} "
            f"fy={self.fy:.2f}"
        )

        # ====================================================
        # WARMUP
        # ====================================================

        log.info("Warming up camera...")

        for _ in range(60):

            self.pipeline.wait_for_frames()

        self.running = True

        log.info("RealSense camera ready")

    # ========================================================
    # GET FRAMES
    # ========================================================

    def get_frames(self):

        if not self.running:
            return None

        try:

            frames = self.pipeline.wait_for_frames()

            aligned = self.align.process(frames)

            depth_frame = aligned.get_depth_frame()

            color_frame = aligned.get_color_frame()

            if not depth_frame or not color_frame:

                log.warning(
                    "Invalid RealSense frames"
                )

                return None

            # ================================================
            # FILTERS
            # ================================================

            depth_frame = self.spatial.process(
                depth_frame
            )

            depth_frame = self.temporal.process(
                depth_frame
            )

            depth_frame = self.hole_filling.process(
                depth_frame
            )

            depth_frame = depth_frame.as_depth_frame()

            # ================================================
            # CONVERT
            # ================================================

            depth_image = np.asanyarray(
                depth_frame.get_data()
            ).astype(np.float32)

            color_image = np.asanyarray(
                color_frame.get_data()
            )

            self.last_frame_time = time.time()

            return {

                "depth": depth_image,

                "color": color_image,

                "depth_frame": depth_frame,

                "color_frame": color_frame,

                "timestamp": self.last_frame_time

            }

        except Exception as e:

            log.error(
                f"Frame acquisition error: {e}"
            )

            return None

    # ========================================================
    # INTRINSICS
    # ========================================================

    def get_intrinsics(self):

        return {

            "fx": self.fx,
            "fy": self.fy,

            "ppx": self.ppx,
            "ppy": self.ppy,

            "width": self.width,
            "height": self.height,

            "depth_scale": self.depth_scale

        }

    # ========================================================
    # HEALTH CHECK
    # ========================================================

    def is_alive(self):

        if not self.running:
            return False

        dt = time.time() - self.last_frame_time

        return dt < 5.0

    # ========================================================
    # STOP
    # ========================================================

    def stop(self):

        log.info("Stopping RealSense pipeline...")

        self.running = False

        try:

            if self.pipeline:

                self.pipeline.stop()

        except Exception as e:

            log.warning(
                f"RealSense pipeline stop error: {e}"
            )

        log.info("RealSense stopped")
