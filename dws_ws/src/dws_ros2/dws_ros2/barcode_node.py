"""
barcode_node.py
===============

Barcode node used as transaction trigger.

FLOW:

USB Scanner
      ↓
BarcodeNode
      ↓
Publish BarcodeMsg
      ↓
Orchestrator starts transaction
"""

import threading
import time
import logging
import glob

from evdev import (
    InputDevice,
    categorize,
    ecodes
)

from dws_ros2.sim_bus import BUS
from dws_ros2.messages import BarcodeMsg


log=logging.getLogger(
    "barcode_node"
)

SCAN_COOLDOWN=2.0


class BarcodeNode:

    def __init__(

        self,
        station_id="STATION_01",
        stub_mode=False

    ):

        self.station_id=station_id
        self.stub_mode=stub_mode

        self._running=False
        self._thread=None

        self._last_scan_time=0
        self._trigger_count=0

        self.barcode_buffer=""
        self.device=None

        self._last_detect_attempt = 0
        self._detect_retry_interval = 10.0

        self._detect_scanner()


    # -----------------------------------
    # Scanner detection
    # -----------------------------------

    def _detect_scanner(self):

        log.info(
            "Searching for scanner..."
        )

        try:

            devices=[

                InputDevice(path)

                for path in

                glob.glob(
                    "/dev/input/event*"
                )

            ]


            for dev in devices:

                name=dev.name.lower()

                SUPPORTED_SCANNERS = (
                    "newland",
                    "nls",
                    "zebra",
                    "symbol",
                    "honeywell",
                    "datalogic",
                    "scanner",
                )

                if any(vendor in name for vendor in SUPPORTED_SCANNERS):

                    self.device = dev

                    log.info(
                        f"\nUSB HID scanner found\n"
                        f"Name: {dev.name}\n"
                        f"Path: {dev.path}"
                    )

                    return

                


        except Exception as e:

            log.warning(

                f"HID detection error:{e}"

            )


        log.warning(
            "No scanner found"
        )

        log.warning(
            "Fallback typing mode enabled"
        )


    # -----------------------------------
    # Lifecycle
    # -----------------------------------

    def start(self):

        self._running=True

        self._thread=threading.Thread(

            target=self._run_loop,
            daemon=True,
            name="barcode_node"

        )

        self._thread.start()

        log.info(
            "BarcodeNode started"
        )


    def stop(self):

        self._running=False

        if self._thread:

            self._thread.join(
                timeout=3
            )



    # -----------------------------------
    # Main loop
    # -----------------------------------

    def _run_loop(self):

        while self._running:

            try:

                self._wait_for_trigger()

            except Exception as e:

                log.warning(
                    f"_wait_for_trigger error (recovering): {e}"
                )

                time.sleep(0.5)

            # Late scanner attachment: if we're currently in
            # fallback mode, periodically retry detection so a
            # scanner plugged in after boot gets picked up
            # without requiring a process restart.
            if self.device is None:

                now = time.time()

                if (
                    now - self._last_detect_attempt
                    >= self._detect_retry_interval
                ):

                    self._last_detect_attempt = now
                    self._detect_scanner()


    # -----------------------------------
    # Trigger
    # -----------------------------------

    def _wait_for_trigger(self):


        # USB scanner mode
        if self.device:

            try:

                for event in self.device.read_loop():

                    if not self._running:
                        return

                    if event.type != ecodes.EV_KEY:
                        continue

                    key=categorize(event)

                    if key.keystate != 1:
                        continue


                    code=key.keycode


                    if code=="KEY_ENTER":

                        if len(
                            self.barcode_buffer
                        )>0:

                            self._publish()

                        return


                    elif code.startswith(
                        "KEY_"
                    ):

                        value=code.replace(
                            "KEY_",
                            ""
                        )


                        if value.isdigit():

                            self.barcode_buffer += value


                        elif len(value)==1:

                            self.barcode_buffer += value

            except OSError as e:

                log.warning(
                    f"USB scanner disconnected ({e}), "
                    f"falling back to manual input mode"
                )

                self.device = None
                self.barcode_buffer = ""
                self._last_detect_attempt = time.time()
                return

        # Manual fallback mode

        else:

            try:

                barcode=input(

                    "\nScan barcode:\n"

                ).strip()

            except EOFError:

                # stdin closed/redirected (e.g. running as a
                # service with no TTY) - don't crash the thread,
                # just back off and let the loop retry.
                time.sleep(1.0)
                return

            if not barcode:
                return

            self.barcode_buffer=barcode

            self._publish()


    def _publish(self):

        now=time.time()

        if (

            now
            -
            self._last_scan_time

            <
            SCAN_COOLDOWN

        ):

            log.warning(
                "Ignored cooldown"
            )

            # FIX: clear buffer even on cooldown
            self.barcode_buffer=""

            return


        msg=BarcodeMsg(

            data=self.barcode_buffer,

            timestamp=now

        )


        subscribers=BUS.publish(

            "/dws/barcode",
            msg

        )


        self._trigger_count +=1

        self._last_scan_time=now


        log.info(

            f"[scanner] "
            f"TX #{self._trigger_count} "
            f"| Barcode={self.barcode_buffer} "
            f"| Subscribers={subscribers}"

        )


        self.barcode_buffer=""



# ---------------------------------------
# ENTRY POINT
# ---------------------------------------

def main(args=None):

    logging.basicConfig(
        level=logging.INFO,
        format='[%(name)s][%(levelname)s] %(message)s'
    )

    node=BarcodeNode()

    try:

        node.start()

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        pass

    finally:

        node.stop()


if __name__=="__main__":
    main()