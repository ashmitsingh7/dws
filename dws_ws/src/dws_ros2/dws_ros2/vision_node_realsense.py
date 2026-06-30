import cv2
import os
import time
import logging
import threading

from dws_ros2.realsense_vision.realsense_camera import (
    RealSenseCamera
)

from dws_ros2.realsense_vision.calibration_manager import (
    CalibrationManager
)

from dws_ros2.realsense_vision.measurement_engine import (
    MeasurementEngine
)

from dws_ros2.realsense_vision.human_detector import (
    HumanDetector
)

from dws_ros2.realsense_vision.visualization import (
    VisualizationManager
)

from dws_ros2.realsense_vision.snapshot_manager import (
    SnapshotManager
)

from dws_ros2.realsense_vision.auto_calibration_v2 import (
    AutoCalibrationV2
)

from dws_ros2 import perception_bridge
from dws_ros2.paths import CALIBRATION_DIR

# ============================================================
# LOGGER
# ============================================================

log = logging.getLogger(
    "vision_node_realsense"
)

# ============================================================
# REQUIRED CALIBRATION FILES
# ============================================================

REQUIRED_CALIBRATION_FILES = [
    "stable_empty_depth.npy",
    "noise_map.npy",
    "system_geometry.npy",
]

# ============================================================
# REALSENSE VISION NODE
# ============================================================

class VisionNodeRealSense:

    # ========================================================
    # INIT
    # ========================================================

    def __init__(self, fps=30, camera_mode="hardware"):

        log.info(
            "Initializing RealSense Vision Node"
        )

        if camera_mode != "hardware":
            # NOTE: this node only drives real RealSense hardware.
            # `vision.camera_mode: simulation` in dws_params.yaml is
            # accepted (so the key exists and doesn't crash config
            # loading) but is NOT implemented here — there is no
            # software depth-frame simulator wired into
            # VisionNodeRealSense. Don't claim otherwise; log it
            # loudly so this limitation is visible, not silent.
            log.warning(
                f"[CONFIG] vision.camera_mode={camera_mode!r} requested, "
                f"but only 'hardware' is implemented in "
                f"VisionNodeRealSense — running against real camera "
                f"hardware regardless."
            )

        # ====================================================
        # CALIBRATION
        # ====================================================

        self.calibration = CalibrationManager()

        success = self.calibration.load()

        self.calibration_valid = success

        if not success:

            log.warning(
                "Calibration not loaded — "
                "run auto calibration before use"
            )

        # ====================================================
        # CAMERA
        # ====================================================
        # NOTE: hardware startup (camera.start()) is intentionally
        # NOT performed here. If RealSense hardware is absent at
        # process boot, constructing the camera object must still
        # succeed so the rest of the system (orchestrator, HMI,
        # database, etc.) can come up and report a clean
        # CAMERA_ERROR instead of crashing the whole launcher with
        # a raw traceback before anything else has even started.
        # Actual hardware start happens in start_camera(), called
        # from start() below, with retries.
        # ====================================================

        self.camera = RealSenseCamera(

            width=self.calibration.frame_width,

            height=self.calibration.frame_height,

            fps=fps

        )

        self.camera_ready = False

        # Guards concurrent access to the camera/engine, shared
        # between the live stream thread and capture_measurement()
        # calls triggered from the orchestrator.
        self._camera_lock = threading.Lock()

        # ====================================================
        # HUMAN DETECTOR
        # ====================================================

        self.human_detector = HumanDetector(

            model_name="yolov8s.pt",

            confidence=0.25,

            imgsz=640

        )

        # ====================================================
        # ENGINE
        # ====================================================

        self.engine = MeasurementEngine(

            calibration=self.calibration,

            human_detector=self.human_detector

        )

        # ====================================================
        # VISUALIZATION
        # ====================================================

        self.visualizer = VisualizationManager()

        # ====================================================
        # SNAPSHOT
        # ====================================================

        self.snapshot_manager = SnapshotManager()

        # ====================================================
        # STATE
        # ====================================================

        self.latest_frame = None

        self._last_raw_depth = None
        self._last_raw_color = None
        self.latest_result = None

        self._latest_jpeg = None

        self._stream_running = False
        self._stream_thread = None

        self._freeze_until = 0

        log.info(

            "RealSense Vision Node Ready "
            "(camera hardware not yet started)"

        )

    # ========================================================
    # CAMERA STARTUP (controlled, retrying, non-fatal)
    # ========================================================

    def start_camera(self, retries=3, retry_delay=2.0):
        """
        Attempt to start the RealSense camera hardware. Never
        raises -- on failure, logs a clean error (no raw
        traceback) and leaves camera_ready=False so the rest of
        the system can boot and surface CAMERA_ERROR/health-check
        failures instead of the whole process dying at import/
        construction time.
        """

        with self._camera_lock:

            for attempt in range(1, retries + 1):

                try:

                    self.camera.start()

                    intr = self.camera.get_intrinsics()

                    self.calibration.update_intrinsics(

                        intr["fx"],
                        intr["fy"],

                        intr["ppx"],
                        intr["ppy"]

                    )

                    self.camera_ready = True

                    log.info(
                        "RealSense camera started "
                        f"(attempt {attempt}/{retries})"
                    )

                    return True

                except Exception as e:

                    log.error(
                        f"[CAMERA] start attempt {attempt}/{retries} "
                        f"failed: {e}"
                    )

                    if attempt < retries:
                        time.sleep(retry_delay)

            log.error(
                "[CAMERA] RealSense hardware unavailable after "
                f"{retries} attempts — continuing without camera. "
                "Connect the device and re-run calibration/health "
                "check, or restart the system once it is attached."
            )

            self.camera_ready = False

            return False

    # ========================================================
    # CALIBRATION FILE CHECK
    # ========================================================

    @staticmethod
    def check_calibration_files():

        missing = []

        for fname in REQUIRED_CALIBRATION_FILES:

            path = os.path.join(
                CALIBRATION_DIR,
                fname
            )

            if not os.path.exists(path):

                missing.append(fname)

        if missing:

            log.warning(
                f"[HEALTH] Missing calibration files: "
                f"{missing}"
            )

            return {
                "valid": False,
                "missing": missing,
                "reason": "CALIBRATION_FILES_MISSING"
            }

        # ----------------------------------------------------
        # Presence alone isn't enough — make sure each file
        # actually loads and (for system_geometry.npy) contains
        # the keys the rest of the system depends on.
        # ----------------------------------------------------

        import numpy as np

        for fname in REQUIRED_CALIBRATION_FILES:

            path = os.path.join(CALIBRATION_DIR, fname)

            try:

                if fname == "system_geometry.npy":

                    geo = np.load(path, allow_pickle=True).item()

                    required_keys = [
                        "floor_depth_mm",
                        "scale_depth_mm",
                        "scale_height_mm",
                        "roi_x1",
                        "roi_y1",
                        "roi_x2",
                        "roi_y2",
                    ]

                    for key in required_keys:

                        if key not in geo:

                            log.warning(
                                f"[HEALTH] system_geometry.npy "
                                f"missing key: {key}"
                            )

                            return {
                                "valid": False,
                                "reason": "MISSING_KEY",
                                "file": fname,
                                "key": key,
                            }

                    for key in required_keys:

                        val = geo.get(key)

                        if not isinstance(val, (int, float)):

                            log.warning(
                                f"[HEALTH] system_geometry.npy "
                                f"key {key} has bad type: "
                                f"{type(val)}"
                            )

                            return {
                                "valid": False,
                                "reason": "CORRUPT_FILE",
                                "file": fname,
                                "key": key,
                            }

                else:

                    arr = np.load(path, allow_pickle=True)

                    if arr is None or getattr(arr, "size", 0) == 0:

                        log.warning(
                            f"[HEALTH] {fname} loaded empty"
                        )

                        return {
                            "valid": False,
                            "reason": "CORRUPT_FILE",
                            "file": fname,
                        }

            except Exception as e:

                log.warning(
                    f"[HEALTH] {fname} failed to load: {e}"
                )

                return {
                    "valid": False,
                    "reason": "CORRUPT_FILE",
                    "file": fname,
                }

        log.info(
            "[HEALTH] Calibration files OK"
        )

        return {"valid": True}

    # ========================================================
    # CAMERA + DEPTH HEALTH CHECK
    # ========================================================

    def check_health(self):

        if not self.camera_ready:

            log.warning(
                "[HEALTH] Camera hardware not started"
            )

            return {
                "valid": False,
                "reason": "CAMERA_NOT_STARTED"
            }

        try:

            with self._camera_lock:
                frames = self.camera.get_frames()

            if frames is None:

                log.warning(
                    "[HEALTH] Camera returned no frames"
                )

                return {
                    "valid": False,
                    "reason": "NO_FRAMES"
                }

            depth = frames.get("depth")
            color = frames.get("color")

            if depth is None:

                log.warning(
                    "[HEALTH] Depth stream not alive"
                )

                return {
                    "valid": False,
                    "reason": "DEPTH_STREAM_DEAD"
                }

            if color is None:

                log.warning(
                    "[HEALTH] Color stream not alive"
                )

                return {
                    "valid": False,
                    "reason": "COLOR_STREAM_DEAD"
                }

            log.info(
                "[HEALTH] Camera unit OK"
            )

            return {"valid": True}

        except Exception as e:

            log.error(
                f"[HEALTH] Camera health check failed: {e}"
            )

            return {
                "valid": False,
                "reason": "CAMERA_ERROR"
            }

    # ========================================================
    # PROCESS ONCE
    # ========================================================

    def process_once(self):

        if not self.camera_ready:
            return None

        with self._camera_lock:
            frames = self.camera.get_frames()

        if frames is None:
            return None

        # ====================================================
        # DICT FORMAT
        # ====================================================

        depth_image = frames["depth"]

        color_image = frames["color"]

        if depth_image is None:
            return None

        if color_image is None:
            return None

        # cache raw frames for one-shot perception_engine v7 refinement
        # at capture time (see capture_measurement / perception_bridge)
        self._last_raw_depth = depth_image
        self._last_raw_color = color_image

        # ====================================================
        # ENGINE
        # ====================================================

        result = self.engine.process(

            color_image,

            depth_image

        )

        log.debug(
            f"[PROCESS] result={result}"
        )

        if result is None:

            log.warning(
                "[PROCESS] engine returned None"
            )

            return None

        log.debug(
            f"[PROCESS] "
            f"valid={result.get('valid')} | "
            f"locked={result.get('locked')} | "
            f"reason={result.get('reason')}"
        )

        # ====================================================
        # VISUALIZATION
        # ====================================================

        annotated = self.visualizer.render(

            color_image,

            result,

            self.calibration,

            self.human_detector

        )

        self.latest_frame = annotated

        self.latest_result = result

        # ====================================================
        # JPEG BUFFER (for MJPEG stream)
        # ====================================================

        try:

            _, jpeg = cv2.imencode(
                ".jpg",
                annotated
            )

            self._latest_jpeg = jpeg.tobytes()

        except Exception:

            pass

        mask = None

        if "mask" in result:

            mask = self.visualizer.render_mask(

                result["mask"]

            )

        return {

            "result": result,

            "frame": annotated,

            "mask": mask

        }

    # ========================================================
    # PROCESS
    # ========================================================

    def process(self):

        output = self.process_once()

        if output is None:
            return None

        return output["result"]

    # ========================================================
    # DEBUG LOOP
    # ========================================================

    def run_debug(self):

        log.info(
            "Starting debug visualization"
        )

        try:

            while True:

                output = self.process_once()

                if output is None:
                    continue

                cv2.imshow(

                    "DWS Measurement",

                    output["frame"]

                )

                if output["mask"] is not None:

                    cv2.imshow(

                        "DWS Mask",

                        output["mask"]

                    )

                key = cv2.waitKey(1)

                if key == ord('q'):
                    break

        finally:

            self.shutdown()

    # ========================================================
    # RELOAD CALIBRATION
    # ========================================================

    def reload_calibration(self):

        log.info(
            "Reloading calibration from disk"
        )

        success = self.calibration.load()

        if not success:

            log.error(
                "Calibration reload failed"
            )

            return False

        intr = self.camera.get_intrinsics()

        self.calibration.update_intrinsics(

            intr["fx"],
            intr["fy"],

            intr["ppx"],
            intr["ppy"]

        )

        self.engine = MeasurementEngine(

            calibration=self.calibration,

            human_detector=self.human_detector

        )

        log.info(
            "Calibration reload complete"
        )

        return True

    # ========================================================
    # RUN AUTO CALIBRATION
    # ========================================================

    def run_auto_calibration(self):

        log.info(
            "Starting auto calibration"
        )

        with self._camera_lock:

            calibrator = AutoCalibrationV2(
                self.camera
            )

            result = calibrator.run()

        if result.get("success"):

            self.reload_calibration()

        return result

    # ========================================================
    # RESET MEASUREMENT STATE
    # ========================================================

    def reset_measurement_state(self):

        log.info(
            "Resetting vision measurement state"
        )

        # =====================================
        # Reset measurement engine
        # =====================================

        self.engine.reset_lock()

        # =====================================
        # Clear cached results
        # =====================================

        self.latest_result = None

        if hasattr(self, "last_result"):
            self.last_result = None

        if hasattr(self, "last_dimensions"):
            self.last_dimensions = None

        # =====================================
        # Reset acquisition metadata
        # =====================================

        self.capture_active = False

        self.current_tx_id = None

        log.info(
            "Vision reset complete"
        )

    # ========================================================
    # CAPTURE MEASUREMENT
    # ========================================================

    def capture_measurement(

        self,

        timeout=10.0,

        tx_id=None,

        weight_kg=None

    ):

        start = time.time()

        while time.time() - start < timeout:

            try:

                output = self.process_once()

            except Exception as e:

                log.error(
                    f"Camera error during acquisition: {e}"
                )

                return {

                    "valid": False,

                    "locked": False,

                    "reason": "CAMERA_ERROR"

                }

            if output is None:
                continue

            result = output["result"]

            if (

                result.get("valid", False)

                and

                result.get("locked", False)

            ):

                snapshot = self.snapshot_manager.save(

                    image=output["frame"],

                    dimensions=result,

                    tx_id=tx_id,

                    weight_kg=weight_kg

                )

                image_path = None

                if snapshot is not None:

                    image_path = snapshot.get(
                        "path",
                        None
                    )

                # -----------------------------------
                # PERCEPTION ENGINE v7 — ONE-SHOT
                # REFINEMENT + POINT CLOUD CAPTURE
                # -----------------------------------
                # Runs only once per locked transaction (not in the
                # live loop), using the raw RGB-D frame cached by
                # process_once(). Failure here never blocks the
                # transaction -- the original engine's L/W/H above
                # remain the values used unless this succeeds.

                pointcloud_path = None
                refined = None

                if (
                    self._last_raw_depth is not None
                    and self._last_raw_color is not None
                ):

                    try:

                        refined = perception_bridge.refine_measurement(
                            color_image=self._last_raw_color,
                            depth_raw_units=self._last_raw_depth,
                            intrinsics_dict=self.camera.get_intrinsics(),
                            calibration_dir=CALIBRATION_DIR,
                            tx_id=tx_id,
                        )

                    except Exception as e:

                        log.error(
                            f"[PERCEPTION_ENGINE_V7] "
                            f"refinement failed: {e}"
                        )

                if refined is not None and refined.get("success"):

                    pointcloud_path = refined.get(
                        "pointcloud_path"
                    )

                    log.info(
                        f"[PERCEPTION_ENGINE_V7] "
                        f"L={refined['length_mm']:.1f}mm "
                        f"W={refined['width_mm']:.1f}mm "
                        f"H={refined['height_mm']:.1f}mm "
                        f"conf={refined['confidence']:.1f}% "
                        f"pts={refined['point_count']} "
                        f"pointcloud={pointcloud_path}"
                    )

                elif refined is not None:

                    log.warning(
                        f"[PERCEPTION_ENGINE_V7] "
                        f"no point cloud saved: "
                        f"{refined.get('failure_reason')}"
                    )

                # -----------------------------------
                # PUSH ANNOTATED FRAME TO LIVE HMI
                # -----------------------------------

                annotated_frame = output["frame"]

                self.latest_frame = annotated_frame.copy()

                ok, jpeg = cv2.imencode(
                    ".jpg",
                    annotated_frame
                )

                if ok:

                    self._latest_jpeg = jpeg.tobytes()

                self._freeze_until = time.time() + 1.0

                return {

                    "valid": True,

                    "length": round(

                        result.get(
                            "length_mm",
                            0
                        ),

                        1

                    ),

                    "width": round(

                        result.get(
                            "breadth_mm",
                            0
                        ),

                        1

                    ),

                    "height": round(

                        result.get(
                            "height_mm",
                            0
                        ),

                        1

                    ),

                    "object_count":
                    result.get(
                        "object_count",
                        1
                    ),

                    "confidence":
                    result.get(
                        "confidence",
                        1.0
                    ),

                    "reason":
                    result.get("reason"),

                    "image_path": image_path,

                    "pointcloud_path": pointcloud_path

                }

        return {

            "valid": False,

            "locked": False,

            "reason": "TIMEOUT"

        }

    # ========================================================
    # GET FRAME
    # ========================================================

    def get_latest_frame(self):

        return self.latest_frame

    # ========================================================
    # GET JPEG FRAME
    # ========================================================

    def get_jpeg_frame(self):

        return self._latest_jpeg

    # ========================================================
    # STREAM LOOP
    # ========================================================

    def _stream_loop(self):

        import cv2
        import time

        log.info(
            "Starting live vision stream thread"
        )

        while self._stream_running:

            if not self.camera_ready:
                time.sleep(0.5)
                continue

            # -----------------------------------
            # HOLD TRANSACTION FRAME
            # -----------------------------------

            if time.time() < self._freeze_until:

                time.sleep(0.03)
                continue

            try:

                log.debug(
                    "[STREAM] requesting frames"
                )

                with self._camera_lock:
                    frames = self.camera.get_frames()

                log.debug(
                    f"[STREAM] raw frames type={type(frames)}"
                )

                if frames is None:

                    log.warning(
                        "[STREAM] frames is None"
                    )

                    time.sleep(0.1)
                    continue

                # -----------------------------------
                # HANDLE FRAME STRUCTURES
                # -----------------------------------

                if isinstance(frames, dict):

                    color_frame = frames.get("color")

                elif isinstance(frames, tuple):

                    color_frame = frames[0]

                else:

                    color_frame = frames

                if color_frame is None:

                    log.warning(
                        "[STREAM] color_frame is None"
                    )

                    time.sleep(0.1)
                    continue

                log.debug(
                    f"[STREAM] frame shape={color_frame.shape}"
                )

                frame = color_frame.copy()

                self.latest_frame = frame

                ok, jpeg = cv2.imencode(
                    ".jpg",
                    frame
                )

                log.debug(
                    f"[STREAM] jpeg encode={ok}"
                )

                if ok:

                    self._latest_jpeg = (
                        jpeg.tobytes()
                    )

                    log.debug(
                        f"[STREAM] jpeg size="
                        f"{len(self._latest_jpeg)}"
                    )

            except Exception as e:

                log.exception(
                    f"[STREAM] ERROR: {e}"
                )

            time.sleep(0.03)

    # ========================================================
    # START
    # ========================================================

    def start(self):

        log.info(
            "Starting vision node"
        )

        self.start_camera()

        if not self.camera_ready:

            log.warning(
                "Vision node starting WITHOUT camera hardware "
                "— live stream/measurement will report errors "
                "until the camera is connected and the node is "
                "restarted or start_camera() is retried."
            )

        self._stream_running = True

        self._stream_thread = threading.Thread(

            target=self._stream_loop,

            daemon=True

        )

        self._stream_thread.start()

        log.info(
            "Vision node started"
        )

    # ========================================================
    # STOP / SHUTDOWN
    # ========================================================
    # Single unified cleanup path. `shutdown()` is kept only as an
    # alias for backwards compatibility with the standalone
    # `ros2 run dws_ros2 vision_node_realsense` entry point below
    # -- there is no longer a second, divergent cleanup
    # implementation that could leak the stream thread.
    # ========================================================

    def stop(self):

        log.info(
            "Shutting down vision node"
        )

        self._stream_running = False

        if self._stream_thread:

            self._stream_thread.join(
                timeout=2
            )

            if self._stream_thread.is_alive():

                log.warning(
                    "[SHUTDOWN] stream thread did not exit "
                    "within timeout"
                )

        if self.camera_ready:

            with self._camera_lock:

                try:
                    self.camera.stop()
                except Exception as e:
                    log.warning(
                        f"[SHUTDOWN] camera.stop() raised: {e}"
                    )

            self.camera_ready = False

        log.info(
            "Vision node shutdown complete"
        )

    def shutdown(self):
        """Alias for stop() -- kept for the standalone entry point."""

        self.stop()

def main(args=None):
    """
    Standalone entry point for `ros2 run dws_ros2 vision_node_realsense`
    (multi-process deployment via dws_launch.py). The single-process
    launcher (run_dws.py) instantiates VisionNodeRealSense directly
    instead -- this wrapper exists only for the multi-process path.
    """

    logging.basicConfig(
        level=logging.INFO,
        format='[%(name)s][%(levelname)s] %(message)s'
    )

    node = VisionNodeRealSense()

    try:

        node.start()

        while True:
            time.sleep(1)

    except KeyboardInterrupt:

        pass

    finally:

        node.shutdown()


if __name__ == "__main__":

    main()
