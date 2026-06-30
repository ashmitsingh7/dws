
import os
import numpy as np

from dws_ros2.paths import CALIBRATION_DIR as CALIB_DIR

# ============================================================
# PATHS
# ============================================================

GEOMETRY_PATH = os.path.join(
    CALIB_DIR,
    "system_geometry.npy"
)

DEPTH_PATH = os.path.join(
    CALIB_DIR,
    "stable_empty_depth.npy"
)

NOISE_PATH = os.path.join(
    CALIB_DIR,
    "noise_map.npy"
)


# ============================================================
# CALIBRATION MANAGER
# ============================================================

class CalibrationManager:

    def __init__(self):

        # ====================================================
        # RAW DATA
        # ====================================================

        self.geometry = None

        self.empty_depth = None

        self.noise_map = None

        # ====================================================
        # FRAME
        # ====================================================

        self.frame_width = 1280
        self.frame_height = 720

        # ====================================================
        # ROI
        # ====================================================

        self.roi_x1 = 0
        self.roi_y1 = 0

        self.roi_x2 = 0
        self.roi_y2 = 0

        # ====================================================
        # SCALE
        # ====================================================

        self.scale_center_x = 0
        self.scale_center_y = 0

        self.scale_depth_mm = 0.0

        # ====================================================
        # INTRINSICS
        # ====================================================

        self.fx = None
        self.fy = None

        self.ppx = None
        self.ppy = None

    # ========================================================
    # LOAD
    # ========================================================

    def load(self):

        if not os.path.exists(GEOMETRY_PATH):

            print("\nMissing geometry file")
            print(GEOMETRY_PATH)

            return False

        if not os.path.exists(DEPTH_PATH):

            print("\nMissing depth file")
            print(DEPTH_PATH)

            return False

        try:

            # =================================================
            # GEOMETRY
            # =================================================

            self.geometry = np.load(
                GEOMETRY_PATH,
                allow_pickle=True
            ).item()

            # =================================================
            # EMPTY DEPTH
            # =================================================

            raw = np.load(
                DEPTH_PATH,
                allow_pickle=True
            )

            if (
                isinstance(raw, np.ndarray)
                and raw.dtype == object
            ):
                raw = raw.item()

            self.empty_depth = np.array(
                raw,
                dtype=np.float32
            )

            # =================================================
            # NOISE MAP
            # =================================================

            if os.path.exists(NOISE_PATH):

                self.noise_map = np.load(
                    NOISE_PATH,
                    allow_pickle=True
                )

            # =================================================
            # ROI
            # =================================================

            self.roi_x1 = int(
                self.geometry["roi_x1"]
            )

            self.roi_y1 = int(
                self.geometry["roi_y1"]
            )

            self.roi_x2 = int(
                self.geometry["roi_x2"]
            )

            self.roi_y2 = int(
                self.geometry["roi_y2"]
            )

            # =================================================
            # SCALE CENTER
            # =================================================

            self.scale_center_x = int(
                self.geometry["scale_center_x"]
            )

            self.scale_center_y = int(
                self.geometry["scale_center_y"]
            )

            # =================================================
            # SCALE DEPTH
            # =================================================

            self.scale_depth_mm = float(
                self.geometry["scale_depth_mm"]
            )

            # =================================================
            # INTRINSICS
            # =================================================

            self.fx = self.geometry.get(
                "fx",
                None
            )

            self.fy = self.geometry.get(
                "fy",
                None
            )

            self.ppx = self.geometry.get(
                "ppx",
                None
            )

            self.ppy = self.geometry.get(
                "ppy",
                None
            )

            print("\nCalibration loaded successfully")

            print(
                f"Depth shape: "
                f"{self.empty_depth.shape}"
            )

            print(
                f"ROI: "
                f"({self.roi_x1}, {self.roi_y1}) "
                f"-> "
                f"({self.roi_x2}, {self.roi_y2})"
            )

            print(
                f"Scale depth: "
                f"{self.scale_depth_mm:.1f} mm"
            )

            return True

        except Exception as e:

            print("\nCalibration load failed")
            print(e)

            return False

    # ========================================================
    # UPDATE INTRINSICS
    # ========================================================

    def update_intrinsics(

        self,

        fx,
        fy,

        ppx,
        ppy

    ):

        self.fx = fx
        self.fy = fy

        self.ppx = ppx
        self.ppy = ppy

        print("\nUpdated camera intrinsics")

        print(
            f"fx={self.fx:.2f} "
            f"fy={self.fy:.2f}"
        )

    # ========================================================
    # GET ROI
    # ========================================================

    def get_roi(self):

        return (

            self.roi_x1,
            self.roi_y1,

            self.roi_x2,
            self.roi_y2

        )

    # ========================================================
    # GET ROI RECT
    # ========================================================

    def get_roi_rect(self):

        return self.get_roi()

    # ========================================================
    # GET WORKSPACE POLYGON
    # ========================================================

    def get_workspace_polygon(self):

        pts = np.array([

            [self.roi_x1, self.roi_y1],

            [self.roi_x2, self.roi_y1],

            [self.roi_x2, self.roi_y2],

            [self.roi_x1, self.roi_y2]

        ], dtype=np.int32)

        return pts

    # ========================================================
    # GET SCALE CENTER
    # ========================================================

    def get_scale_center(self):

        return (

            self.scale_center_x,
            self.scale_center_y

        )

    # ========================================================
    # GET SCALE DEPTH
    # ========================================================

    def get_scale_depth(self):

        return self.scale_depth_mm

    # ========================================================
    # GET EMPTY DEPTH
    # ========================================================

    def get_empty_depth(self):

        return self.empty_depth

    # ========================================================
    # GET NOISE MAP
    # ========================================================

    def get_noise_map(self):

        return self.noise_map

    # ========================================================
    # GET INTRINSICS
    # ========================================================

    def get_intrinsics(self):

        return {

            "fx": self.fx,
            "fy": self.fy,

            "ppx": self.ppx,
            "ppy": self.ppy

        }
