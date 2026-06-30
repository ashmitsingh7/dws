"""
scale_node.py
=============

Industrial-grade weighing scale node
for DWS systems.

Features:
- rolling statistical stability detection
- spread analysis
- standard deviation analysis
- consecutive stable window confirmation
- serial scale integration
- stable mean estimation
- industrial noise tolerance
- silent continuous acquisition
"""

import threading
import time
import random
import logging
import re
import statistics

from collections import deque

from dws_ros2.messages import ScaleMsg


log = logging.getLogger("scale_node")


# ---------------------------------------------------
# Industrial tuning parameters
# ---------------------------------------------------
# These module-level constants remain as fallback defaults (mirrored
# in config_loader.DEFAULTS["scale"]). A running node uses the
# per-instance attributes set in __init__ below, fed from
# dws_params.yaml via config_loader in run_dws.py — module globals
# are no longer read directly during stability computation.

ZERO_THRESHOLD = 0.05

# 20 samples ≈ 2 second statistical window
BUFFER_SIZE = 20

# allowable max-min fluctuation
SPREAD_THRESHOLD = 0.03

# allowable statistical noise
STD_DEV_THRESHOLD = 0.012

# stable windows required before asserting stable
REQUIRED_STABLE_WINDOWS = 8


class ScaleNode:

    def __init__(
        self,
        port="/dev/ttyUSB0",
        baudrate=9600,
        station_id="STATION_01",
        stub_mode=False,
        buffer_size=BUFFER_SIZE,
        spread_threshold=SPREAD_THRESHOLD,
        std_dev_threshold=STD_DEV_THRESHOLD,
        required_stable_windows=REQUIRED_STABLE_WINDOWS,
        zero_threshold=ZERO_THRESHOLD,
    ):

        self.port = port
        self.baudrate = baudrate
        self.station_id = station_id
        self.stub_mode = stub_mode

        # stability tuning — configurable via dws_params.yaml
        self.buffer_size = buffer_size
        self.spread_threshold = spread_threshold
        self.std_dev_threshold = std_dev_threshold
        self.required_stable_windows = required_stable_windows
        self.zero_threshold = zero_threshold

        # rolling sample buffer
        self.weight_buffer = []

        # latest live state
        self.latest_weight = 0.0
        self.latest_stable = False
        self.latest_timestamp = 0.0

        # diagnostics
        self.latest_std_dev = 0.0
        self.latest_mean = 0.0
        self.latest_spread = 0.0

        # industrial stability tracking
        self.stable_counter = 0
        self.last_stable_weight = 0.0

        # optional debug queue
        self.weight_queue = deque(maxlen=20)

        self._running = False
        self._thread = None
        self._ser = None

        self._state_lock = threading.Lock()

        # debug state transition tracking
        self._last_logged_stable = None

    # ---------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------

    def start(self):

        self._running = True

        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="scale_node"
        )

        self._thread.start()

        log.info(
            f"ScaleNode started "
            f"(stub={self.stub_mode})"
        )

    def stop(self):

        self._running = False

        if self._thread:

            self._thread.join(timeout=3)

        if self._ser:

            try:
                self._ser.close()

            except Exception:
                pass

        log.info("ScaleNode stopped")

    # ---------------------------------------------------
    # Main loop
    # ---------------------------------------------------

    def _run_loop(self):

        while self._running:

            if self.stub_mode:

                self._poll_stub()

            else:

                self._poll_serial()

            time.sleep(0.02)

    # ---------------------------------------------------
    # Stub mode
    # ---------------------------------------------------

    def _poll_stub(self):

        raw = random.uniform(
            5.000,
            5.015
        )

        self._ingest_reading(
            raw,
            "US"
        )

    # ---------------------------------------------------
    # Serial mode
    # ---------------------------------------------------

    def _poll_serial(self):

        if self._ser is None:

            self._connect_serial()
            return

        try:

            raw_bytes = self._ser.read_until(
                b'\r'
            )

            raw = raw_bytes.decode(
                "ascii",
                errors="ignore"
            )

            raw = (
                raw
                .replace('\x02', '')
                .replace('\x03', '')
                .replace('\n', '')
                .replace('\r', '')
                .strip()
            )

            if raw:

                match = re.search(
                    r'[-+]?\d*\.?\d+',
                    raw
                )

                if match:

                    weight = float(
                        match.group()
                    )

                    self._ingest_reading(
                        weight,
                        "ST"
                    )

        except Exception as e:

            log.warning(
                f"Serial:{e}"
            )

            try:
                self._ser.close()

            except Exception:
                pass

            self._ser = None

    def _connect_serial(self):

        try:

            import serial

            self._ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=1
            )

            log.info(
                f"Connected:{self.port}"
            )

        except Exception as e:

            log.error(f"{e}")

            time.sleep(2)

    # ---------------------------------------------------
    # Industrial statistical stability logic
    # ---------------------------------------------------

    def _compute_statistics(self):

        if len(self.weight_buffer) < self.buffer_size:

            return {
                "stable": False,
                "mean": 0.0,
                "std_dev": 999.0,
                "spread": 999.0
            }

        samples = self.weight_buffer[-self.buffer_size:]

        mean_weight = statistics.mean(
            samples
        )

        std_dev = statistics.pstdev(
            samples
        )

        spread = (
            max(samples)
            -
            min(samples)
        )

        statistically_stable = (

            spread < self.spread_threshold

            and

            std_dev < self.std_dev_threshold

        )

        # consecutive stable validation
        if statistically_stable:

            self.stable_counter += 1

        else:

            self.stable_counter = 0

        stable = (

            self.stable_counter
            >=
            self.required_stable_windows

        )

        return {

            "stable": stable,

            "mean": mean_weight,

            "std_dev": std_dev,

            "spread": spread
        }

    # ---------------------------------------------------
    # Reading ingestion
    # ---------------------------------------------------

    def _ingest_reading(
        self,
        raw_val,
        cas_status
    ):

        self.weight_buffer.append(
            raw_val
        )

        if len(self.weight_buffer) > self.buffer_size:

            self.weight_buffer.pop(0)

        stats = self._compute_statistics()

        stable = stats["stable"]

        mean_weight = stats["mean"]

        std_dev = stats["std_dev"]

        spread = stats["spread"]

        # force unstable near zero
        if mean_weight < self.zero_threshold:

            stable = False
            self.stable_counter = 0

        # save latest state (thread-safe snapshot for get_measurement)
        with self._state_lock:
            self.latest_weight = round(
                mean_weight,
                3
            )

            self.latest_stable = stable

            self.latest_timestamp = time.time()

            self.latest_std_dev = round(
                std_dev,
                5
            )

            self.latest_mean = round(
                mean_weight,
                3
            )

            self.latest_spread = round(
                spread,
                5
            )

            if stable:
                self.last_stable_weight = mean_weight

        # ---------------------------------------------------
        # ONLY LOG STATE TRANSITIONS
        # ---------------------------------------------------

        if self._last_logged_stable != stable:

            if stable:

                log.info(

                    f"[STABLE] "
                    f"{mean_weight:.3f} kg | "
                    f"spread={spread:.5f} | "
                    f"std={std_dev:.5f}"

                )

            else:

                log.info(
                    "[UNSTABLE] Scale readings fluctuating"
                )

            self._last_logged_stable = stable

        # ---------------------------------------------------
        # Create message
        # ---------------------------------------------------

        msg = ScaleMsg(

            weight=self.latest_weight,

            unit="kg",

            timestamp=self.latest_timestamp,

            stable=self.latest_stable

        )

        self.weight_queue.append(msg)

    # ---------------------------------------------------
    # Snapshot API
    # ---------------------------------------------------

    def get_measurement(self):

        with self._state_lock:
            return ScaleMsg(

                weight=self.latest_weight,

                unit="kg",

                timestamp=self.latest_timestamp,

                stable=self.latest_stable

            )

    def get_latest(self):

        return self.get_measurement()

    # ---------------------------------------------------
    # Health check
    # ---------------------------------------------------

    def check_health(self):

        if not self._running:

            log.warning(
                "[HEALTH] Scale node not running"
            )

            return {
                "valid": False,
                "reason": "SCALE_NOT_RUNNING"
            }

        if self.stub_mode:

            log.info(
                "[HEALTH] Scale OK (stub mode)"
            )

            return {"valid": True}

        if self._ser is None:

            log.warning(
                "[HEALTH] Scale serial not connected"
            )

            return {
                "valid": False,
                "reason": "SCALE_SERIAL_DISCONNECTED"
            }

        deadline = time.time() + 3.0

        while time.time() < deadline:

            if self.latest_timestamp > 0:

                age = time.time() - self.latest_timestamp

                if age < 2.0:

                    log.info(
                        f"[HEALTH] Scale OK "
                        f"| last reading {age:.2f}s ago"
                    )

                    return {"valid": True}

            time.sleep(0.1)

        log.warning(
            "[HEALTH] Scale not producing readings"
        )

        return {
            "valid": False,
            "reason": "SCALE_NO_READINGS"
        }


# ---------------------------------------------------
# Entry point
# ---------------------------------------------------

def main(args=None):

    logging.basicConfig(
        level=logging.INFO,
        format='[%(name)s][%(levelname)s] %(message)s'
    )

    try:
        from dws_ros2.config_loader import get_config
        sc = get_config()["scale"]
        node = ScaleNode(
            port=sc["serial_port"],
            baudrate=sc["baudrate"],
            buffer_size=sc["buffer_size"],
            spread_threshold=sc["spread_threshold_kg"],
            std_dev_threshold=sc["std_dev_threshold_kg"],
            required_stable_windows=sc["required_stable_windows"],
            zero_threshold=sc["zero_threshold_kg"],
        )
    except Exception as e:
        log.warning(f"[CONFIG] Falling back to built-in defaults: {e}")
        node = ScaleNode()

    try:

        node.start()

        while True:

            time.sleep(1)

    except KeyboardInterrupt:

        pass

    finally:

        node.stop()


if __name__ == "__main__":

    main()