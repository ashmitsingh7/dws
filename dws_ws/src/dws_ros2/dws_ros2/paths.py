"""
paths.py
========

Single source of truth for every filesystem path the DWS pipeline
needs at runtime (HMI assets, capture images, point clouds,
calibration files, the SQLite DB).

Build 006 change: all runtime-generated data is written to a single
``dws_runtime/`` directory that lives beside ``dws_ws/`` in the
repository root.  Nothing is ever written into install/, build/, or
src/.

Runtime root layout:
    dws_runtime/
        database/
        captures/
        pointclouds/
        calibration/
        backups/
        exports/
        logs/

Resolution order:
1. Explicit DWS_* environment variable override (always wins if set).
2. Source-checkout layout  — dws_runtime/ beside dws_ws/.
3. colcon install/ layout  — dws_runtime/ beside the install prefix.
4. Legacy ~/dws_runtime/ fallback.

Every consumer imports constants from here; no module independently
re-derives its own paths.
"""

import os

# ============================================================
# PACKAGE ROOT DETECTION
# ============================================================

# .../dws_ros2/dws_ros2/paths.py
_THIS_FILE = os.path.abspath(__file__)

# .../dws_ros2/dws_ros2
_PKG_DIR = os.path.dirname(_THIS_FILE)

# .../dws_ros2  (source checkout package root, one level up)
_SRC_CHECKOUT_ROOT = os.path.dirname(_PKG_DIR)


def _is_source_checkout() -> bool:
    """
    True when running from a source checkout
    (<root>/dws_ros2/dws_ros2/paths.py with a sibling hmi/ dir).
    """
    return os.path.isdir(os.path.join(_SRC_CHECKOUT_ROOT, "hmi"))


def _colcon_install_root():
    """
    Walk up from this file looking for a colcon install prefix
    (the directory that contains a share/dws_ros2 sibling).
    Returns None if not running from an install tree.
    """
    here = _PKG_DIR
    for _ in range(6):
        candidate_share = os.path.join(
            os.path.dirname(here), "share", "dws_ros2"
        )
        if os.path.isdir(candidate_share):
            return os.path.dirname(here)
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return None


# ============================================================
# RUNTIME ROOT
# ============================================================
# All runtime-generated data lives under one root so it is
# never accidentally inside install/, build/, or src/.

def _resolve_runtime_root() -> str:
    """
    Resolve the single dws_runtime/ root directory.

    Priority:
    1. DWS_RUNTIME_ROOT env var (operator override)
    2. Beside dws_ws/ in a source checkout
    3. Beside the colcon install prefix
    4. ~/dws_runtime/ legacy fallback
    """
    override = os.environ.get("DWS_RUNTIME_ROOT")
    if override:
        return os.path.abspath(os.path.expanduser(override))

    if _is_source_checkout():
        # _SRC_CHECKOUT_ROOT == .../dws_ws/src/dws_ros2
        # dws_ws == two levels up; repo root == three levels up
        dws_ws = os.path.dirname(os.path.dirname(_SRC_CHECKOUT_ROOT))
        repo_root = os.path.dirname(dws_ws)
        return os.path.join(repo_root, "dws_runtime")

    install_root = _colcon_install_root()
    if install_root:
        repo_root = os.path.dirname(install_root)
        return os.path.join(repo_root, "dws_runtime")

    return os.path.join(os.path.expanduser("~"), "dws_runtime")


RUNTIME_ROOT = _resolve_runtime_root()


def _runtime(env_var, *sub):
    """
    Return an absolute path inside RUNTIME_ROOT, overridable by env_var.
    """
    override = os.environ.get(env_var)
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(RUNTIME_ROOT, *sub)


# ============================================================
# RESOLVED PATHS
# ============================================================

# HMI static assets live in src (read-only); resolved separately.
HMI_DIR = os.environ.get("DWS_HMI_DIR") or (
    os.path.join(_SRC_CHECKOUT_ROOT, "hmi")
    if _is_source_checkout()
    else os.path.join(
        _colcon_install_root() or os.path.expanduser("~"),
        "share", "dws_ros2", "hmi"
    )
)

# Runtime-written directories — all inside dws_runtime/
CAPTURE_DIR     = _runtime("DWS_CAPTURE_DIR",    "captures")
POINTCLOUD_DIR  = _runtime("DWS_POINTCLOUD_DIR", "pointclouds")
# Calibration files are read AND written by the realsense_vision subsystem.
# They live inside the source tree (realsense_vision/calibration/) in a
# source checkout, or beside this module in a colcon install tree.
# This matches the pre-Build006 behaviour so existing .npy files are found
# without any migration step. The DWS_CALIBRATION_DIR env var overrides.
def _resolve_calibration_dir() -> str:
    override = os.environ.get("DWS_CALIBRATION_DIR")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    if _is_source_checkout():
        # _SRC_CHECKOUT_ROOT is .../dws_ws/src/dws_ros2
        return os.path.join(
            _SRC_CHECKOUT_ROOT, "dws_ros2", "realsense_vision", "calibration"
        )
    install_root = _colcon_install_root()
    if install_root:
        return os.path.join(_PKG_DIR, "realsense_vision", "calibration")
    return os.path.join(
        os.path.expanduser("~"), "dws_ws", "src", "dws_ros2",
        "dws_ros2", "realsense_vision", "calibration"
    )

CALIBRATION_DIR = _resolve_calibration_dir()
BACKUP_DIR      = _runtime("DWS_BACKUP_DIR",     "backups")
EXPORT_DIR      = _runtime("DWS_EXPORT_DIR",     "exports")
LOG_DIR         = _runtime("DWS_LOG_DIR",        "logs")

DATABASE_DIR    = _runtime("DWS_DATABASE_DIR",   "database")


# ============================================================
# DATABASE PATH
# ============================================================

def resolve_db_path() -> str:
    """
    Resolve the SQLite database path.

    Priority:
    1. DWS_DB_PATH environment variable (always wins)
    2. database.file from dws_params.yaml (relative → under DATABASE_DIR)
    3. Built-in default dws_runtime/database/dws_data.db
    """
    override = os.environ.get("DWS_DB_PATH")
    if override:
        return os.path.abspath(os.path.expanduser(override))

    db_file = "dws_data.db"
    try:
        from dws_ros2.config_loader import get_config
        db_file = get_config()["database"]["file"]
    except Exception:
        pass

    if os.path.isabs(db_file):
        return db_file

    # Relative paths are resolved under the runtime database/ dir
    return os.path.join(DATABASE_DIR, db_file)


# DB_PATH used by api_server, hmi_bridge_node, and database_node default.
DB_PATH = resolve_db_path()


# ============================================================
# DIRECTORY CREATION
# ============================================================

def ensure_dirs():
    """
    Create all writable runtime directories on startup.
    Never raises — a missing directory must never crash startup.
    """
    dirs = [
        DATABASE_DIR,
        CAPTURE_DIR,
        POINTCLOUD_DIR,
        BACKUP_DIR,
        EXPORT_DIR,
        LOG_DIR,
    ]
    for d in dirs:
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as exc:
            import logging
            logging.getLogger("paths").warning(
                f"[paths] Could not create directory {d}: {exc}"
            )
