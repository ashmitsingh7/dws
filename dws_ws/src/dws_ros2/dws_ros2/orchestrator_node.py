"""
orchestrator_node.py
====================

Industrial-style triggered DWS FSM.

Features:
- barcode-triggered acquisition
- continuous stability validation
- statistical stable hold window
- duplicate suppression
- multiple-object retry logic
- Camera Unit single-shot triggering
- proper terminal error cancellation
- ghost transaction prevention
- SQLite transaction persistence
- error image preservation support
- startup calibration lock
- 90/100 shipment calibration warning/lock
- calibration FSM with pass/fail persistence
"""

import time
import math
import logging
import uuid
import threading
import os

from dws_ros2.sim_bus import BUS

from dws_ros2.messages import (

    BarcodeMsg,
    TransactionMsg,
    SaveTransactionRequest

)


log = logging.getLogger(
    "orchestrator_node"
)


# ============================================================
# VISION BACKEND
# ============================================================

USE_REALSENSE = True

if USE_REALSENSE:

    from dws_ros2.vision_node_realsense import (
        VisionNodeRealSense as VisionNode
    )

else:

    from dws_ros2.vision_node import VisionNode


DIM_FACTOR = 5000.0

MIN_WEIGHT = 0.05

BARCODE_DEDUP_WINDOW = 2.0

# weight must remain stable
# continuously for this duration
REQUIRED_STABLE_TIME = 2.0

# total timeout allowed
STABILITY_TIMEOUT = 10.0

# ============================================================
# SCALE ZERO VERIFICATION
# ============================================================
# Weight must remain within threshold continuously for this
# duration before the system returns to IDLE.
# The software NEVER sends a tare command — the operator presses
# the physical TARE button on the scale.
# ============================================================

ZERO_THRESHOLD_KG      = 0.02   # abs(weight) <= this → near-zero
ZERO_STABLE_DURATION_S = 1.0    # must hold for this long

# ============================================================
# DEVELOPER MODE
# ------------------------------------------------------------
# Source of truth is the persisted `system_state.developer_mode`
# column (see database_node.py), loaded once at boot into
# self._developer_mode by _load_persisted_calibration_state().
# This module-level constant is ONLY the fallback used before
# the DB has ever been read (fresh install, DB unreachable at
# boot) — it is intentionally True so Developer Mode is the
# sprint default per Build 004. Flip the persisted DB value
# (via /dws/update_system_state or a future Settings UI) to
# switch to Production Mode; do not hardcode False here, that
# would silently re-introduce the old env-var-only behaviour.
# ============================================================

DEVELOPER_MODE_DEFAULT = True


class OrchestratorNode:

    def __init__(

        self,

        scale_node,
        vision_node,

        dim_factor=DIM_FACTOR,

        station_id="STATION_01",

        zero_verify_threshold_kg=ZERO_THRESHOLD_KG,
        zero_verify_stable_duration_s=ZERO_STABLE_DURATION_S

    ):

        self.scale_node = scale_node

        self.vision_node = vision_node

        self.dim_factor = dim_factor

        self.station_id = station_id

        self._zero_verify_threshold    = zero_verify_threshold_kg
        self._zero_verify_duration     = zero_verify_stable_duration_s

        self._running = False

        self._dev_mode_poll_thread = None

        self._last_awb = ""
        self._last_awb_time = 0

        self._session_id = str(uuid.uuid4())  # unique ID for this process run

        self._total_transactions = 0

        self._rejected_empty = 0
        self._rejected_unstable = 0
        self._rejected_duplicate = 0
        self._rejected_vision = 0
        self._rejected_multiple = 0

        # -----------------------------------
        # Transaction lifecycle protection
        # -----------------------------------

        self._current_tx_failed = False
        self._current_awb = None
        self._active_tx_id = None

        # -----------------------------------
        # Calibration state
        # -----------------------------------

        self._shipments_since_calibration = 0

        self._calibration_required = True

        self._calibration_locked = False

        self._calibration_warning_sent = False

        # Developer Mode: when True, calibration status is still
        # tracked and reported honestly (see _calibration_required/
        # _calibration_locked above and /health, /system) but never
        # blocks scanning/weighing/measurement. Overwritten by the
        # persisted system_state value in
        # _load_persisted_calibration_state() during boot.
        self._developer_mode = DEVELOPER_MODE_DEFAULT

        self._system_state = "BOOTING"

        # -------------------------------------------------------
        # Scale zero verification gate
        # True while WAIT_SCALE_ZERO is active; barcode callback
        # must return immediately when this flag is set.
        # -------------------------------------------------------
        self._waiting_scale_zero = False

        # -----------------------------------
        # Concurrency protection
        # -----------------------------------
        # Guards: shared FSM/system_state mutation, and
        # prevents two calibration runs (manual box-cal +
        # auto-cal wizard) from executing concurrently.
        # -----------------------------------

        self._state_lock = threading.Lock()

        self._calibration_lock = threading.Lock()

    # ===================================
    # Lifecycle
    # ===================================

    def start(self):

        self._running = True

        BUS.subscribe(

            "/dws/barcode",

            self._barcode_callback

        )

        # -----------------------------------
        # Restore calibration state persisted
        # from a previous run, so a successful
        # calibration survives a restart.
        # -----------------------------------

        self._load_persisted_calibration_state()

        # Start the developer_mode live-poll thread (see
        # _dev_mode_poll_loop docstring above set_developer_mode).
        # Daemon + same _running flag as the rest of this node, so
        # it shares this process's lifecycle and never blocks exit.
        self._dev_mode_poll_thread = threading.Thread(
            target=self._dev_mode_poll_loop,
            daemon=True,
            name="dev_mode_poll"
        )
        self._dev_mode_poll_thread.start()

        # Open a shift session in the database
        BUS.call_service(
            "/dws/open_session",
            {
                "session_id": self._session_id,
                "station_id": self.station_id,
                "operator": ""
            }
        )

        # ===================================
        # BOOT FSM
        # ===================================

        # -----------------------------------
        # CHECKING_CAMERA
        # -----------------------------------

        self._set_system_state(
            "CHECKING_CAMERA"
        )

        if self.vision_node is None:

            raise RuntimeError(
                "Vision node failed to initialize"
            )

        camera_health = (
            self.vision_node.check_health()
        )

        if not camera_health.get("valid"):

            raise RuntimeError(
                f"Camera unit health check failed: "
                f"{camera_health.get('reason')}"
            )

        log.info(
            f"[BOOT] Camera unit OK "
            f"({'RealSense' if USE_REALSENSE else 'Legacy'})"
        )

        # -----------------------------------
        # CHECKING_CALIBRATION
        # -----------------------------------

        self._set_system_state(
            "CHECKING_CALIBRATION"
        )

        cal_check = (
            self.vision_node
            .check_calibration_files()
        )

        if not cal_check.get("valid"):

            log.warning(
                f"[BOOT] Calibration files missing: "
                f"{cal_check.get('missing')}"
            )

            self._require_calibration()

            if self._developer_mode:

                log.warning(
                    "[BOOT][DEV MODE] Calibration missing — "
                    "status recorded honestly but boot is NOT "
                    "halted, Developer Mode is enabled"
                )

                BUS.publish(
                    "/dws/calibration_warning",
                    {
                        "count": self._shipments_since_calibration,
                        "dev_bypass": True,
                        "reason": "calibration_files_missing"
                    }
                )

                # fall through to continue the boot FSM below
                # instead of halting at CALIBRATION_REQUIRED

            else:

                self._set_system_state(
                    "CALIBRATION_REQUIRED"
                )

                log.info(
                    "[BOOT] System waiting for calibration"
                )

                return

        if not self.vision_node.calibration_valid:

            log.warning(
                "[BOOT] Calibration could not be loaded"
            )

            self._require_calibration()

            if self._developer_mode:

                log.warning(
                    "[BOOT][DEV MODE] Calibration could not be "
                    "loaded — status recorded honestly but boot "
                    "is NOT halted, Developer Mode is enabled"
                )

                BUS.publish(
                    "/dws/calibration_warning",
                    {
                        "count": self._shipments_since_calibration,
                        "dev_bypass": True,
                        "reason": "calibration_load_failed"
                    }
                )

                # fall through to continue the boot FSM below

            else:

                self._set_system_state(
                    "CALIBRATION_REQUIRED"
                )

                return

        log.info(
            "[BOOT] Calibration OK"
        )

        # -----------------------------------
        # CHECKING_SCALE
        # -----------------------------------

        self._set_system_state(
            "CHECKING_SCALE"
        )

        scale_ok = False

        for _ in range(10):

            scale_health = (
                self.scale_node.check_health()
            )

            if scale_health.get("valid"):

                scale_ok = True
                break

            log.warning(
                "[BOOT] Waiting for scale connection..."
            )

            time.sleep(1)

        if not scale_ok:

            raise RuntimeError(
                f"Scale health check failed: "
                f"{scale_health.get('reason')}"
            )

        log.info(
            "[BOOT] Scale OK"
        )

        # -----------------------------------
        # READY
        # -----------------------------------
        # NOTE: calibration requirement is intentionally NOT
        # forced here. It is either already True (set above if
        # calibration files were missing/invalid) or restored
        # from persisted system_state by
        # _load_persisted_calibration_state(). Forcing it True
        # unconditionally on every boot would mean a successful
        # calibration never survives a restart.

        log.info(
            "OrchestratorNode started"
        )

        self._set_system_state("IDLE")

    def stop(self):

        self._running = False

        if self._dev_mode_poll_thread:
            self._dev_mode_poll_thread.join(timeout=3)

        # Close the shift session
        BUS.call_service(
            "/dws/close_session",
            {"session_id": self._session_id}
        )

        log.info(
            "OrchestratorNode stopped"
        )

    # ===================================
    # Transaction reset
    # ===================================

    def _reset_transaction_state(self):

        with self._state_lock:
            self._current_tx_failed = False
            self._current_awb = None
            self._active_tx_id = None

    # ===================================
    # Vision state reset helper
    # ===================================

    def _reset_vision_state(self):

        log.info(
            f"[TX={self._active_tx_id}] "
            f"Resetting vision state"
        )

        if hasattr(
            self.vision_node,
            "reset_measurement_state"
        ):

            self.vision_node.reset_measurement_state()

        log.info(
            f"[TX={self._active_tx_id}] "
            f"Vision state reset complete"
        )

    # ===================================
    # System state helper
    # ===================================

    def _set_system_state(self, state):

        with self._state_lock:
            self._system_state = state
            active_tx_id = self._active_tx_id

        payload = {

            "state": state,

            "tx_id":
                active_tx_id,

            "timestamp":
                time.time()
        }

        BUS.publish(
            "/dws/system_state",
            payload
        )

        log.info(
            f"[SYSTEM_STATE] {state}"
        )

    # ===================================
    # Calibration helpers
    # ===================================

    def _is_calibration_awb(self, awb):

        boxes = BUS.call_service(

            "/dws/get_calibration_boxes",

            {}

        ) or []

        return any(

            box["awb"] == awb

            for box in boxes

        )

    def _require_calibration(self):

        self._calibration_required = True

        BUS.publish(

            "/dws/calibration_required",

            {

                "required": True,

                "timestamp": time.time(),

                "developer_mode": self._developer_mode

            }

        )

    def _load_persisted_calibration_state(self):
        """
        Restore calibration_required / calibration_locked /
        shipments_since_calibration from the system_state table
        so a successful calibration survives a process restart.
        Falls back to the safe "calibration required" default if
        the DB isn't reachable yet (e.g. fresh DB, no prior run).
        """

        try:

            state = BUS.call_service(
                "/dws/get_system_state",
                {}
            )

        except Exception as e:

            log.warning(
                f"[BOOT] Could not load persisted system_state: {e}"
            )

            state = None

        if not state:

            log.info(
                "[BOOT] No persisted system_state found "
                "— defaulting to calibration required"
            )

            return

        self._calibration_required = bool(
            state.get("calibration_required", 1)
        )

        self._calibration_locked = bool(
            state.get("calibration_locked", 0)
        )

        self._shipments_since_calibration = int(
            state.get("shipments_since_calibration", 0) or 0
        )

        self._developer_mode = bool(
            state.get("developer_mode", DEVELOPER_MODE_DEFAULT)
        )

        log.info(
            f"[BOOT] Restored persisted calibration state "
            f"| required={self._calibration_required} "
            f"| locked={self._calibration_locked} "
            f"| shipments={self._shipments_since_calibration} "
            f"| developer_mode={self._developer_mode}"
        )

    def _persist_calibration_state(self):
        """
        Push the current calibration flags to the system_state
        table. Best-effort: a DB hiccup here must never break
        the calibration FSM itself.
        """

        try:

            BUS.call_service(

                "/dws/update_system_state",

                {

                    "calibration_required":
                    int(self._calibration_required),

                    "calibration_locked":
                    int(self._calibration_locked),

                    "shipments_since_calibration":
                    self._shipments_since_calibration,

                    "last_calibration_timestamp":
                    time.time()

                }

            )

        except Exception as e:

            log.warning(
                f"[CAL] Failed to persist system_state: {e}"
            )

    def set_developer_mode(self, enabled: bool):
        """
        Toggle Developer Mode at runtime and persist it. Usable by
        any in-process caller (e.g. a BUS-based settings handler
        running inside this same process). NOT the channel used by
        api_server.py — that's a separate OS process and cannot
        reach this method or the in-process BUS directly (SimBus is
        explicitly single-process). api_server.py's POST /settings
        writes system_state.developer_mode straight to the DB
        instead; this orchestrator picks that up live via
        _dev_mode_poll_loop() below, not by anyone calling this
        setter cross-process.
        """

        self._developer_mode = bool(enabled)

        log.info(
            f"[CONFIG] developer_mode set to "
            f"{self._developer_mode}"
        )

        try:

            BUS.call_service(
                "/dws/update_system_state",
                {"developer_mode": int(self._developer_mode)}
            )

        except Exception as e:

            log.warning(
                f"[CONFIG] Failed to persist developer_mode: {e}"
            )

    # ===================================
    # Developer Mode live poll
    # ------------------------------------
    # api_server.py runs as a separate OS process (its own uvicorn
    # server) with no BUS access — SimBus is explicitly in-process
    # only. So POST /settings (api_server.py) cannot call
    # set_developer_mode() above or any BUS service on this
    # orchestrator directly; it can only write system_state.
    # developer_mode straight into the DB. Without this loop, that
    # write would silently sit unread until the next process
    # restart, since _load_persisted_calibration_state() only runs
    # once, at start(). This thread closes that gap by re-reading
    # the same column on an interval and live-updating
    # self._developer_mode — mirrors HealthMonitor's exact
    # interval-thread pattern (health_monitor.py) rather than
    # inventing new infrastructure.
    # ===================================

    DEV_MODE_POLL_INTERVAL_SECONDS = 5

    def _dev_mode_poll_loop(self):

        while self._running:

            time.sleep(
                self.DEV_MODE_POLL_INTERVAL_SECONDS
            )

            if not self._running:
                break

            try:

                state = BUS.call_service(
                    "/dws/get_system_state",
                    {}
                )

            except Exception as e:

                log.warning(
                    f"[CONFIG] developer_mode poll: "
                    f"could not read system_state: {e}"
                )

                continue

            if not state:
                continue

            if "developer_mode" not in state:
                # Pre-migration DB row read mid-migration race, or
                # a get_system_state() call that hit a stale
                # connection — skip this cycle rather than guess.
                continue

            new_value = bool(state.get("developer_mode"))

            if new_value != self._developer_mode:

                log.info(
                    f"[CONFIG] developer_mode changed externally "
                    f"(likely via POST /settings): "
                    f"{self._developer_mode} -> {new_value}"
                )

                self._developer_mode = new_value

    # ===================================
    # Terminal error helper
    # ===================================

    def _raise_error(

        self,

        error_type,
        awb,
        message,
        image_path=None

    ):

        # -----------------------------------
        # Mark transaction terminated
        # -----------------------------------

        self._current_tx_failed = True

        self._set_system_state("ERROR")

        # -----------------------------------
        # Reset vision state on every failure
        # -----------------------------------

        self._reset_vision_state()

        payload = {

            "error_type":
            error_type,

            "awb":
            awb,

            "message":
            message,

            "timestamp":
            time.time(),

            # preserve image for HMI audit
            "image_path":
            image_path,

            "station_id":
            self.station_id,

            "tx_id":
            self._active_tx_id,

            # derive severity: hardware errors are HIGH, others MEDIUM
            "severity": (
                "HIGH"
                if error_type in (
                    "CAMERA_TIMEOUT", "SCALE_TIMEOUT",
                    "SCANNER_TIMEOUT", "DB_SERVICE_FAILED",
                    "DB_INSERT_FAILED"
                )
                else "MEDIUM"
            ),

            # derive component from error_type prefix
            "component": (
                "hardware"
                if any(error_type.startswith(p) for p in (
                    "CAMERA", "SCALE", "SCANNER", "BARCODE"
                ))
                else "processing"
            ),

        }

        # -----------------------------------
        # Save DB
        # -----------------------------------

        BUS.call_service(

            "/dws/save_error",

            payload

        )

        # -----------------------------------
        # Publish HMI popup
        # -----------------------------------

        BUS.publish(

            "/dws/error",

            payload

        )

        log.warning(

            f"[ERROR] "
            f"{error_type} "
            f"| AWB={awb} "
            f"| {message}"

        )

        # -----------------------------------
        # RECOVERY
        # -----------------------------------
        # ERROR is a transient terminal state for the *current*
        # transaction, not a permanent system state. Clear the
        # failed transaction and return the FSM to a state that
        # can accept the next trigger, otherwise the HMI is stuck
        # displaying ERROR forever after any single failure.
        # -----------------------------------

        self._reset_transaction_state()

        if self._calibration_required and not self._developer_mode:

            self._set_system_state("CALIBRATION_REQUIRED")

        else:

            self._set_system_state("IDLE")

    # ===================================
    # Calibration FSM
    # ===================================

    def _run_calibration(

        self,

        awb

    ):
        """
        Concurrency guard wrapper -- prevents the legacy box-flow
        calibration from running at the same time as the
        auto-calibration wizard (start_auto_calibration), since both
        ultimately drive the same camera hardware. The real
        implementation lives in _run_calibration_impl() so this
        wrapper doesn't have to touch every return path inside it.
        """

        if not self._calibration_lock.acquire(blocking=False):

            log.warning(
                "[CAL] Calibration already in progress "
                "— ignoring duplicate trigger"
            )

            BUS.publish(

                "/dws/calibration_failed",

                {
                    "awb": awb,
                    "reason": "ALREADY_IN_PROGRESS",
                }

            )

            return

        try:

            self._run_calibration_impl(awb)

        finally:

            self._calibration_lock.release()

    def _run_calibration_impl(

        self,

        awb

    ):

        log.info(

            f"[CAL] "
            f"Running calibration "
            f"| AWB={awb}"

        )

        BUS.publish(

            "/dws/calibration_started",

            {

                "awb": awb

            }

        )

        # -----------------------------------
        # Fetch reference box from DB
        # -----------------------------------

        boxes = BUS.call_service(

            "/dws/get_calibration_boxes",

            {}

        ) or []

        ref = next(

            (
                b for b in boxes
                if b["awb"] == awb
            ),

            None

        )

        if ref is None:

            log.error(

                f"[CAL] "
                f"No reference box found "
                f"for AWB={awb}"

            )

            return

        tolerance = ref.get(
            "tolerance_percent",
            5
        )

        # -----------------------------------
        # Wait for stable weight
        # (reuse same stability FSM)
        # -----------------------------------

        stable = False

        scale = None

        start = time.time()

        stable_start = None

        while (

            time.time() - start

            <

            STABILITY_TIMEOUT

        ):

            scale = (

                self.scale_node
                .get_measurement()

            )

            if scale.weight < MIN_WEIGHT:

                log.warning(

                    "[CAL] "
                    "No parcel on scale"

                )

                BUS.publish(

                    "/dws/calibration_failed",

                    {

                        "awb": awb,
                        "reason": "NO_PARCEL"

                    }

                )

                self._calibration_required = True
                self._calibration_locked = True

                return

            if scale.stable:

                if stable_start is None:

                    stable_start = time.time()

                stable_duration = (

                    time.time()
                    -
                    stable_start

                )

                if (

                    stable_duration

                    >=

                    REQUIRED_STABLE_TIME

                ):

                    stable = True
                    break

            else:

                stable_start = None

            time.sleep(0.1)

        if not stable:

            log.warning(

                "[CAL] "
                "Weight unstable during calibration"

            )

            BUS.publish(

                "/dws/calibration_failed",

                {

                    "awb": awb,
                    "reason": "WEIGHT_UNSTABLE"

                }

            )

            self._calibration_required = True
            self._calibration_locked = True

            return

        # -----------------------------------
        # Capture vision
        # -----------------------------------

        self._reset_vision_state()

        dims = (

            self.vision_node
            .capture_measurement()

        )

        if not dims.get("valid", False):

            reason = dims.get(
                "reason",
                "VISION_CAPTURE_FAILED"
            )

            log.warning(

                f"[CAL] "
                f"Vision acquisition failed: "
                f"{reason}"

            )

            BUS.publish(

                "/dws/calibration_failed",

                {

                    "awb": awb,
                    "reason": reason

                }

            )

            self._calibration_required = True
            self._calibration_locked = True

            return

        image_path = dims.get(

            "image_path",

            f"images/{awb}_cal.jpg"

        )

        count = dims.get(
            "object_count",
            1
        )

        # -----------------------------------
        # Compare vs reference
        # -----------------------------------

        meas_l = dims["length"]
        meas_w = dims["width"]
        meas_h = dims["height"]
        meas_wt = scale.weight

        ref_l = ref["length_mm"]
        ref_w = ref["width_mm"]
        ref_h = ref["height_mm"]
        ref_wt = ref["weight_kg"]

        err_l = abs(meas_l - ref_l)
        err_w = abs(meas_w - ref_w)
        err_h = abs(meas_h - ref_h)
        err_wt = abs(meas_wt - ref_wt)

        tol_l = ref_l * tolerance / 100.0
        tol_w = ref_w * tolerance / 100.0
        tol_h = ref_h * tolerance / 100.0
        tol_wt = ref_wt * tolerance / 100.0

        dim_pass = (

            err_l <= tol_l
            and
            err_w <= tol_w
            and
            err_h <= tol_h

        )

        wt_pass = err_wt <= tol_wt

        count_pass = count == 1

        image_pass = bool(image_path)

        # overall confidence: fraction
        # of checks that passed
        checks = [
            dim_pass,
            wt_pass,
            count_pass,
            image_pass
        ]

        confidence = sum(
            1 for c in checks if c
        ) / len(checks)

        overall_pass = all(checks)

        # -----------------------------------
        # Build result dict
        # -----------------------------------

        result = {

            "box_awb": awb,

            "pass_fail": 1 if overall_pass else 0,

            "length_error_mm": err_l,
            "width_error_mm": err_w,
            "height_error_mm": err_h,

            "weight_error_kg": err_wt,

            "count_error": abs(count - 1),

            "image_received": 1 if image_pass else 0,

            "confidence": confidence,

            "override_used": 0

        }

        # -----------------------------------
        # Persist calibration history
        # -----------------------------------

        BUS.call_service(

            "/dws/save_calibration_result",

            result

        )

        # -----------------------------------
        # Pass or fail
        # -----------------------------------

        if overall_pass:

            log.info(

                f"[CAL] PASS "
                f"| AWB={awb} "
                f"| conf={confidence:.2f}"

            )

            self._calibration_required = False

            self._calibration_locked = False

            self._shipments_since_calibration = 0

            self._calibration_warning_sent = False

            self._persist_calibration_state()

            BUS.publish(

                "/dws/calibration_pass",

                result

            )

        else:

            log.warning(

                f"[CAL] FAIL "
                f"| AWB={awb} "
                f"| conf={confidence:.2f} "
                f"| dim_pass={dim_pass} "
                f"| wt_pass={wt_pass} "
                f"| count_pass={count_pass}"

            )

            self._calibration_required = True

            self._calibration_locked = True

            BUS.publish(

                "/dws/calibration_failed",

                result

            )

    # ===================================
    # Barcode callback
    # ===================================

    def _barcode_callback(

        self,
        msg

    ):

        if not self._running:
            return

        # -----------------------------------
        # Block barcode during scale zero verification
        # -----------------------------------

        if self._waiting_scale_zero:

            log.info(
                "[FSM] Barcode ignored — "
                "WAIT_SCALE_ZERO in progress"
            )

            return

        # -----------------------------------
        # Reset transaction context
        # -----------------------------------

        self._reset_transaction_state()

        awb = msg.data.strip()

        with self._state_lock:
            self._current_awb = awb
            self._active_tx_id = str(uuid.uuid4())

        if not awb:

            self._rejected_empty += 1

            self._raise_error(

                "EMPTY_BARCODE",

                "UNKNOWN",

                "Empty barcode scanned"

            )

            return

        # -----------------------------------
        # Check if this is a calibration box
        # -----------------------------------

        is_cal_box = self._is_calibration_awb(
            awb
        )

        # -----------------------------------
        # Calibration gate
        # -----------------------------------

        if self._calibration_required:

            if not is_cal_box:

                if self._developer_mode:

                    log.warning(

                        "[DEV MODE] Calibration required "
                        "but not enforced — Developer Mode "
                        "is enabled"

                    )

                    BUS.publish(

                        "/dws/calibration_warning",

                        {

                            "count":
                            self._shipments_since_calibration,

                            "dev_bypass": True,

                            "reason":
                            "calibration_required_dev_mode"

                        }

                    )

                else:

                    self._raise_error(

                        "CALIBRATION_REQUIRED",

                        awb,

                        "Calibration required before processing shipments."

                    )

                    return

        # -----------------------------------
        # Route calibration AWBs directly
        # -----------------------------------

        if is_cal_box:

            self._run_calibration(
                awb
            )

            return

        now = time.time()

        # -----------------------------------
        # Duplicate suppression
        # -----------------------------------

        if (

            awb == self._last_awb

            and

            (
                now
                -
                self._last_awb_time
            )

            <

            BARCODE_DEDUP_WINDOW

        ):

            self._rejected_duplicate += 1

            self._raise_error(

                "DUPLICATE_BARCODE",

                awb,

                "Duplicate barcode detected"

            )

            return

        self._last_awb = awb
        self._last_awb_time = now

        self._set_system_state(
            "WAITING_FOR_WEIGHT"
        )

        # ===================================
        # TRUE CONTINUOUS STABILITY WINDOW
        # ===================================

        log.info(

            f"[FSM] "
            f"Waiting for continuously stable "
            f"weight ({REQUIRED_STABLE_TIME}s) "
            f"| AWB={awb}"

        )

        stable = False

        scale = None

        start = time.time()

        stable_start = None

        while (

            time.time() - start

            <

            STABILITY_TIMEOUT

        ):

            scale = (

                self.scale_node
                .get_measurement()

            )

            # -------------------------------
            # No parcel
            # -------------------------------

            if scale.weight < MIN_WEIGHT:

                self._rejected_empty += 1

                self._raise_error(

                    "NO_PARCEL",

                    awb,

                    "No parcel detected on weighing scale"

                )

                return

            # -------------------------------
            # Stable continuously?
            # -------------------------------

            if scale.stable:

                if stable_start is None:

                    stable_start = time.time()

                    self._set_system_state(
                        "STABILIZING"
                    )

                    log.info(

                        "[FSM] "
                        "Stable timer started"

                    )

                stable_duration = (

                    time.time()
                    -
                    stable_start

                )

                # must remain stable continuously
                if (

                    stable_duration

                    >=

                    REQUIRED_STABLE_TIME

                ):

                    stable = True
                    break

            else:

                stable_start = None

            time.sleep(0.1)

        # -----------------------------------
        # Stability timeout
        # -----------------------------------

        if not stable:

            self._rejected_unstable += 1

            self._raise_error(

                "WEIGHT_UNSTABLE_TIMEOUT",

                awb,

                "Please stand clear from weighing scale and rescan."

            )

            return

        log.info(

            f"[FSM] "
            f"Weight stable continuously "
            f"for {REQUIRED_STABLE_TIME}s "
            f"| AWB={awb}"

        )

        # ===================================
        # Trigger Camera Unit
        # ===================================

        self._set_system_state(
            "CAPTURING"
        )

        self._reset_vision_state()

        dims = (

            self.vision_node
            .capture_measurement(
                tx_id=self._active_tx_id,
                weight_kg=scale.weight
            )

        )

        # -----------------------------------
        # Late callback protection
        # -----------------------------------

        if self._current_tx_failed:
            return

        # ===================================
        # Vision acquisition failure
        # ===================================

        if not dims.get("valid", False):

            self._rejected_vision += 1

            reason = dims.get(
                "reason",
                "VISION_CAPTURE_FAILED"
            )

            image_path = dims.get(
                "image_path",
                None
            )

            log.warning(

                f"[TX={self._active_tx_id}] "
                f"Vision acquisition failed: "
                f"{reason}"

            )

            self._raise_error(

                reason,

                awb,

                reason.replace("_", " "),

                image_path=image_path

            )

            return

        # -----------------------------------
        # Preserve image path EARLY
        # -----------------------------------

        image_path = dims.get(

            "image_path",

            f"images/{awb}.jpg"

        )

        pointcloud_path = dims.get(

            "pointcloud_path",

            None

        )

        confidence = dims.get(
            "confidence",
            1.0
        )

        l = dims["length"]
        w = dims["width"]
        h = dims["height"]

        count = dims.get(
            "object_count",
            1
        )

        # ===================================
        # Multiple object rejection
        # ===================================

        if count > 1:

            log.warning(

                f"[TX={self._active_tx_id}] "
                f"Multiple objects detected: "
                f"{count}"

            )

            self._rejected_multiple += 1

            self._raise_error(

                "MULTIPLE_OBJECTS",

                awb,

                f"{count} parcels detected",

                image_path=image_path

            )

            return

        # ===================================
        # No object
        # ===================================

        if count == 0:

            self._rejected_vision += 1

            self._raise_error(

                "NO_OBJECT_DETECTED",

                awb,

                "No object detected by vision system.",

                image_path=image_path

            )

            return

        # -----------------------------------
        # Final protection against
        # ghost transaction creation
        # -----------------------------------

        if self._current_tx_failed:

            log.warning(

                "[FSM] "
                "Transaction cancelled due to "
                "prior terminal error"

            )

            return

        # ===================================
        # Volumetric calculations
        # ===================================

        vol_weight = math.ceil(

            (
                l
                *
                w
                *
                h
            )

            /

            self.dim_factor

        )

        chargeable = max(

            scale.weight,

            float(vol_weight)

        )

        # ===================================
        # Create transaction
        # ===================================

        t_now = time.time()
        latency_ms = (t_now - self._last_awb_time) * 1000.0 if self._last_awb_time else 0.0

        txn = TransactionMsg(

            awb = awb,

            tx_id = self._active_tx_id,

            length = l,
            width = w,
            height = h,

            weight = scale.weight,

            object_count = count,

            volumetric_weight =
            float(vol_weight),

            chargeable_weight =
            chargeable,

            timestamp =
            t_now,

            sync_status = 0,

            station_id =
            self.station_id,

            image_url =
            image_path,

            pointcloud_url =
            (pointcloud_path or ""),

            dim_factor =
            self.dim_factor,

            latency_ms = latency_ms,

            operator = "",

            confidence = confidence

        )

        # ===================================
        # Save DB
        # ===================================

        self._set_system_state(
            "SAVING"
        )

        req = SaveTransactionRequest(

            transaction = txn

        )

        resp = BUS.call_service(

            "/dws/save_transaction",

            req

        )

        # -----------------------------------
        # Final late callback protection
        # -----------------------------------

        if self._current_tx_failed:

            log.warning(

                "[FSM] "
                "Discarding transaction due to "
                "prior terminal failure"

            )

            return

        if resp is None:

            self._raise_error(

                "DB_SERVICE_FAILED",

                awb,

                "Database service returned None",

                image_path=image_path

            )

            return

        if not resp.success:

            self._raise_error(

                "DB_INSERT_FAILED",

                awb,

                resp.message,

                image_path=image_path

            )

            return

        self._total_transactions += 1

        # -----------------------------------
        # Shipment counter
        # -----------------------------------

        self._shipments_since_calibration += 1

        self._persist_calibration_state()

        # -----------------------------------
        # 90-shipment warning
        # -----------------------------------

        if (

            self._shipments_since_calibration >= 90

            and

            not self._calibration_warning_sent

        ):

            BUS.publish(

                "/dws/calibration_warning",

                {

                    "count":
                    self._shipments_since_calibration

                }

            )

            self._calibration_warning_sent = True

        # -----------------------------------
        # 100-shipment lock
        # -----------------------------------

        if self._shipments_since_calibration >= 100:

            self._require_calibration()

            self._persist_calibration_state()

        # ===================================
        # Publish live update
        # ===================================

        BUS.publish(

            "/dws/transaction",

            txn

        )

        # ===================================
        # Terminal output
        # ===================================

        log.info(

            "\n"
            "=============================\n"
            "DWS TRANSACTION COMPLETE\n"
            "=============================\n"

            f"AWB   : {awb}\n"

            f"TX_ID : {self._active_tx_id}\n"

            f"L     : {l} mm\n"
            f"W     : {w} mm\n"
            f"H     : {h} mm\n"

            f"COUNT : {count}\n"

            f"WT    : {scale.weight:.2f} kg\n"

            f"VWT   : {vol_weight}\n"

            f"CHG   : {chargeable}\n"

            f"IMG   : {image_path}\n"

            "============================="

        )

        # -----------------------------------
        # Reset state after successful txn
        # -----------------------------------

        # -----------------------------------
        # Scale zero verification before
        # returning to IDLE for next transaction
        # -----------------------------------

        self._wait_scale_zero()

    # ===================================
    # SCALE ZERO VERIFICATION
    # ===================================

    def _wait_scale_zero(self):

        """
        Blocks until the scale reads near-zero (abs <= ZERO_THRESHOLD_KG)
        continuously for ZERO_STABLE_DURATION_S seconds.

        Guides the operator via HMI system_state messages.
        Never sends a tare command to the scale.
        """

        self._waiting_scale_zero = True

        try:

            # ------------------------------------------
            # Prompt operator to remove parcel
            # ------------------------------------------

            self._set_system_state("REMOVE_PARCEL")

            log.info(
                "[SCALE_ZERO] Waiting for parcel removal"
            )

            zero_stable_start = None

            while True:

                scale = self.scale_node.get_measurement()

                weight = scale.weight

                near_zero = abs(weight) <= self._zero_verify_threshold

                if near_zero:

                    if zero_stable_start is None:

                        zero_stable_start = time.time()

                        self._set_system_state(
                            "WAIT_SCALE_ZERO"
                        )

                        log.info(
                            "[SCALE_ZERO] Near-zero — "
                            "waiting for stability"
                        )

                    elif (

                        time.time() - zero_stable_start
                        >= self._zero_verify_duration

                    ):

                        # Scale confirmed zero — done
                        break

                else:

                    # Reset stable timer
                    zero_stable_start = None

                    # Residual weight — tell operator
                    self._set_system_state(
                        "SCALE_NOT_ZERO"
                    )

                time.sleep(0.05)

            self._set_system_state(
                "SCALE_ZERO_VERIFIED"
            )

            log.info(
                "[SCALE_ZERO] Scale zero verified — "
                "returning to IDLE"
            )

            # Brief display of success message
            time.sleep(1.0)

        finally:

            self._waiting_scale_zero = False

            self._set_system_state("IDLE")

    # ===================================
    # LIVE FRAME ACCESS
    # ===================================

    def get_live_frame(self):

        if hasattr(

            self.vision_node,

            "get_latest_frame"

        ):

            return self.vision_node.get_latest_frame()

        return None

    # ===================================
    # JPEG FRAME ACCESS
    # ===================================

    def get_live_jpeg(self):

        if hasattr(

            self.vision_node,

            "get_jpeg_frame"

        ):

            return self.vision_node.get_jpeg_frame()

        return None

    # ===================================
    # Metrics
    # ===================================

    def get_metrics(self):

        errors = (

            self._rejected_empty

            +

            self._rejected_unstable

            +

            self._rejected_duplicate

            +

            self._rejected_vision

            +

            self._rejected_multiple

        )

        total = (

            self._total_transactions

            +

            errors

        )

        fault_rate = 0.0

        if total > 0:

            fault_rate = (

                errors
                /
                total

            ) * 100.0

        return {

            "success_count":
            self._total_transactions,

            "error_count":
            errors,

            "fault_rate":
            round(
                fault_rate,
                2
            ),

            "shipments_since_calibration":
            self._shipments_since_calibration,

            "calibration_required":
            self._calibration_required,

            "calibration_locked":
            self._calibration_locked

        }

    # ===================================
    # Auto calibration
    # ===================================

    def start_auto_calibration(self):

        if not self._calibration_lock.acquire(blocking=False):

            log.warning(
                "[AUTO_CAL] Calibration already in progress "
                "— ignoring duplicate trigger"
            )

            BUS.publish(

                "/dws/calibration_failed",

                {
                    "reason": "ALREADY_IN_PROGRESS",
                    "cal_type": "auto_cal",
                }

            )

            return

        try:

            self._set_system_state(
                "CALIBRATION_RUNNING"
            )

            try:

                result = (
                    self.vision_node
                    .run_auto_calibration()
                )

            except Exception as e:

                log.error(
                    f"[AUTO_CAL] Exception: {e}"
                )

                BUS.publish(

                    "/dws/calibration_failed",

                    {
                        "reason": "EXCEPTION",
                        "cal_type": "auto_cal",
                    }

                )

                # Recoverable: an exception during calibration does
                # not change whether calibration was already
                # required, it just failed to clear it. Recover the
                # FSM instead of leaving it stuck in ERROR.
                self._set_system_state(
                    "CALIBRATION_REQUIRED"
                    if self._calibration_required
                    else "IDLE"
                )

                return

            if result.get("success"):

                # Load geometry to send actual values to HMI
                _geo = {}
                try:
                    import numpy as np
                    from dws_ros2.paths import CALIBRATION_DIR
                    _geo_path = os.path.join(
                        CALIBRATION_DIR, "system_geometry.npy"
                    )
                    if os.path.exists(_geo_path):
                        _geo = np.load(_geo_path, allow_pickle=True).item()
                except Exception:
                    pass

                # Bug fix: a successful auto-calibration must clear
                # the calibration-required/locked flags exactly like
                # a successful manual (box) calibration does --
                # previously this branch only changed system_state,
                # leaving _calibration_required/_calibration_locked
                # untouched, so the HMI lock never actually released.
                self._calibration_required = False
                self._calibration_locked = False
                self._shipments_since_calibration = 0
                self._calibration_warning_sent = False

                self._persist_calibration_state()

                BUS.publish(

                    "/dws/calibration_pass",

                    {
                        "timestamp":    time.time(),
                        "cal_type":     "auto_cal",
                        "floor_depth":  _geo.get("floor_depth_mm", 0),
                        "scale_depth":  _geo.get("scale_depth_mm", 0),
                        "scale_height": _geo.get("scale_height_mm", 0),
                        "roi_x1":       _geo.get("roi_x1", 0),
                        "roi_y1":       _geo.get("roi_y1", 0),
                        "roi_x2":       _geo.get("roi_x2", 0),
                        "roi_y2":       _geo.get("roi_y2", 0),
                        "confidence":   1.0
                    }

                )

                self._set_system_state("IDLE")

            else:

                BUS.publish(

                    "/dws/calibration_failed",

                    {
                        "reason": result.get("reason"),
                        "cal_type": "auto_cal",
                    }

                )

                self._calibration_required = True
                self._calibration_locked = True

                self._persist_calibration_state()

                if self._developer_mode:

                    log.warning(
                        "[DEV MODE] Auto-calibration failed — "
                        "status recorded honestly but system "
                        "is returned to IDLE, not blocked"
                    )

                    self._set_system_state("IDLE")

                else:

                    self._set_system_state("CALIBRATION_REQUIRED")

        finally:

            self._calibration_lock.release()


def main(args=None):
    """
    Standalone entry point registered for ros2 run compatibility.

    The supported runtime is the single-process launcher (run_dws.py).
    Multi-process ROS2 launch with SimBus is not supported.
    """
    logging.basicConfig(
        level=logging.INFO,
        format='[%(name)s][%(levelname)s] %(message)s',
    )
    log.error(
        "orchestrator_node must run inside run_dws.py (single process). "
        "Use: ros2 run dws_ros2 run_dws"
    )
    raise SystemExit(1)