"""
config_loader.py
=================

Single source of truth for *runtime-tunable* settings (scale port,
stability timing, retention period, station id, ports, ...).

Before this module existed, `config/dws_params.yaml` was present in
the repo but nothing actually read it — every node hardcoded its own
defaults in source. This module is what makes the YAML file real:

    from dws_ros2.config_loader import get_config
    cfg = get_config()
    cfg["scale"]["serial_port"]

Resolution order for the YAML file itself (mirrors paths.py):
1. `DWS_CONFIG_PATH` environment variable, if set.
2. Source-checkout layout: <repo>/config/dws_params.yaml
3. colcon install layout: <install>/share/dws_ros2/config/dws_params.yaml
4. Legacy fallback: ~/dws_ws/src/dws_ros2/config/dws_params.yaml

If the file is missing, empty, or fails to parse, every node still
gets a complete, sane set of DEFAULTS — a bad/missing config file
must never prevent the system from starting. Parse problems are
logged loudly (not silently swallowed) so a typo gets noticed.

Individual YAML keys may also be overridden by environment variables
of the form `DWS_<SECTION>_<KEY>` (e.g. `DWS_SCALE_SERIAL_PORT`) for
quick overrides without editing the file — useful in deploy/CI.
"""

import os
import copy
import logging

import yaml

log = logging.getLogger("config_loader")


# ============================================================
# DEFAULTS — used whenever the YAML file is missing a key (or
# missing entirely). These match the previous hardcoded values
# in each node, so an absent/blank config file changes nothing.
# ============================================================

DEFAULTS = {
    "system": {
        "station_id": "STATION_01",
        "simulation_mode": False,
    },
    "scale": {
        "serial_port": "/dev/ttyUSB0",
        "baudrate": 9600,
        "publish_rate_hz": 20,
        # stability detection tuning (see scale_node.py)
        "buffer_size": 20,
        "spread_threshold_kg": 0.03,
        "std_dev_threshold_kg": 0.012,
        "required_stable_windows": 8,
        "zero_threshold_kg": 0.05,
    },
    "vision": {
        # "hardware" is the only mode actually implemented by
        # VisionNodeRealSense (see vision_node_realsense.py). Setting
        # this to "simulation" is accepted but logs a warning and is
        # a no-op — there is no software depth simulator wired in.
        "camera_mode": "hardware",
        "fps": 30,
    },
    "database": {
        # Relative filenames are resolved under the same directory
        # paths.DB_PATH would otherwise use (~ by default, or
        # DWS_DB_PATH if set). An absolute path here is used as-is.
        "file": "dws_data.db",
        "retention_days": 30,
    },
    "api": {
        "host": "0.0.0.0",
        "port": 8000,
    },
    "hmi": {
        "host": "0.0.0.0",
        # websocket_port / http_port: served by hmi_bridge_node (in-process).
        # static_port: optional separate python -m http.server (deploy/run.sh).
        "websocket_port": 8765,
        "http_port": 8090,
        "static_port": 8080,
    },
    "health": {
        "check_interval_seconds": 5,
        "min_free_disk_gb": 2.0,
    },
    "sync": {
        # Disabled by default — enable only when cloud_url points to a real backend.
        "enabled": False,
        "cloud_url": "https://api.dws-cloud.example.com/v1/sync",
        "poll_interval_seconds": 10.0,
    },
}


def _resolve_config_path():
    override = os.environ.get("DWS_CONFIG_PATH")
    if override:
        return os.path.abspath(os.path.expanduser(override))

    this_dir = os.path.dirname(os.path.abspath(__file__))          # .../dws_ros2/dws_ros2
    src_root = os.path.dirname(this_dir)                            # .../dws_ros2

    # Source checkout: <repo>/dws_ros2/config/dws_params.yaml
    candidate = os.path.join(src_root, "config", "dws_params.yaml")
    if os.path.isfile(candidate):
        return candidate

    # colcon install layout
    here = this_dir
    for _ in range(6):
        candidate = os.path.join(os.path.dirname(here), "share", "dws_ros2", "config", "dws_params.yaml")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent

    # Legacy fallback (even if it doesn't exist yet — caller handles missing file)
    return os.path.join(os.path.expanduser("~"), "dws_ws", "src", "dws_ros2", "config", "dws_params.yaml")


CONFIG_PATH = _resolve_config_path()


def _deep_merge(base, override):
    """Recursively merge `override` dict onto a copy of `base`."""
    result = copy.deepcopy(base)
    if not isinstance(override, dict):
        return result
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _apply_env_overrides(cfg):
    """Allow DWS_<SECTION>_<KEY> env vars to override individual leaf values."""
    for section, keys in cfg.items():
        if not isinstance(keys, dict):
            continue
        for key, current in keys.items():
            env_name = f"DWS_{section.upper()}_{key.upper()}"
            if env_name in os.environ:
                raw = os.environ[env_name]
                # cast to the same type as the default so downstream code
                # doesn't have to guess (bool/int/float/str)
                try:
                    if isinstance(current, bool):
                        cfg[section][key] = raw.strip().lower() in ("1", "true", "yes", "on")
                    elif isinstance(current, int):
                        cfg[section][key] = int(raw)
                    elif isinstance(current, float):
                        cfg[section][key] = float(raw)
                    else:
                        cfg[section][key] = raw
                except ValueError:
                    log.warning(
                        f"[CONFIG] Env override {env_name}={raw!r} could not "
                        f"be cast to {type(current).__name__}; ignoring"
                    )
    return cfg


_cached_config = None


def load_config(force_reload=False):
    """
    Load DEFAULTS, merge the YAML file on top (if present and valid),
    then apply any DWS_<SECTION>_<KEY> environment overrides.

    Cached after first call; pass force_reload=True to re-read disk
    (used by the HMI Settings page after a save).
    """
    global _cached_config

    if _cached_config is not None and not force_reload:
        return _cached_config

    cfg = copy.deepcopy(DEFAULTS)

    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                raw = yaml.safe_load(f)
            if raw:
                if not isinstance(raw, dict):
                    log.warning(
                        f"[CONFIG] {CONFIG_PATH} did not parse as a mapping "
                        f"(got {type(raw).__name__}); using defaults"
                    )
                else:
                    cfg = _deep_merge(cfg, raw)
                    log.info(f"[CONFIG] Loaded {CONFIG_PATH}")
            else:
                log.warning(f"[CONFIG] {CONFIG_PATH} is empty; using defaults")
        except yaml.YAMLError as e:
            log.error(
                f"[CONFIG] {CONFIG_PATH} failed to parse ({e}); "
                f"falling back to built-in defaults"
            )
        except OSError as e:
            log.error(
                f"[CONFIG] Could not read {CONFIG_PATH} ({e}); "
                f"falling back to built-in defaults"
            )
    else:
        log.warning(
            f"[CONFIG] No config file at {CONFIG_PATH}; using built-in defaults"
        )

    cfg = _apply_env_overrides(cfg)
    _cached_config = cfg
    return cfg


def get_config():
    """Public accessor — returns the cached (or freshly loaded) config dict."""
    return load_config()


def save_config(new_cfg):
    """
    Persist `new_cfg` (a full config dict, same shape as DEFAULTS) back to
    CONFIG_PATH and invalidate the cache. Used by the HMI Settings page.
    """
    global _cached_config

    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(new_cfg, f, default_flow_style=False, sort_keys=False)

    _cached_config = None
    log.info(f"[CONFIG] Saved {CONFIG_PATH}")
    return load_config(force_reload=True)


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(load_config(), indent=2))
