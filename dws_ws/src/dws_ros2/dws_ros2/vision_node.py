"""
vision_node.py
==============

Triggered Cognex acquisition node.

Architecture:
- Cognex maintains TCP connection to this node
- Orchestrator explicitly requests capture
- START trigger command sent over SAME socket
- Cognex returns JSON result
- Listener thread parses packets continuously

Future-ready for:
- FTP image association
- 2D image ingestion
- remote capture storage
"""

import threading
import time
import logging
import socket
import json

from collections import deque

from dws_ros2.messages import DimensionsMsg


log = logging.getLogger(
    "vision_node"
)


COGNEX_IP = "192.168.20.37"

# ORIGINAL WORKING PORT
COGNEX_PORT = 5000

CAPTURE_TIMEOUT = 3.0


class VisionNode:

    def __init__(

        self,
        station_id="STATION_01",
        stub_mode=False

    ):

        self.station_id = station_id
        self.stub_mode = stub_mode

        self._running = False

        self._listener_thread = None

        self.measurement_queue = deque(
            maxlen=20
        )

        self.server = None
        self.client = None

        # latest values
        self.latest_length = 0.0
        self.latest_width = 0.0
        self.latest_height = 0.0
        self.latest_volume = 0.0

        self.latest_count = 1

        self.latest_timestamp = 0.0

        self.client_lock = threading.Lock()

    # -----------------------------------
    # Start / Stop
    # -----------------------------------

    def start(self):

        self._running = True

        self._listener_thread = threading.Thread(

            target=self._listener_loop,
            daemon=True

        )

        self._listener_thread.start()

        log.info(
            "VisionNode started"
        )

    def stop(self):

        self._running = False

        try:

            if self.client:
                self.client.close()

        except:
            pass

        try:

            if self.server:
                self.server.close()

        except:
            pass

    # -----------------------------------
    # Trigger send
    # -----------------------------------

    def _send_trigger(self):

        try:

            with self.client_lock:

                if not self.client:

                    log.warning(
                        "No Cognex client connected"
                    )

                    return False

                log.info(
                    "[Vision] Sending START trigger"
                )

                # VERIFIED WORKING COMMAND
                self.client.sendall(
                    b"START\r\n"
                )

                return True

        except Exception as e:

            log.warning(
                f"Trigger send error:{e}"
            )

            try:

                self.client.close()

            except:
                pass

            self.client = None

            return False

    # -----------------------------------
    # Public capture API
    # -----------------------------------

    def capture_measurement(self):

        self.measurement_queue.clear()

        ok = self._send_trigger()

        if not ok:
            return None

        start = time.time()

        while (

            time.time() - start

            <

            CAPTURE_TIMEOUT

        ):

            if len(
                self.measurement_queue
            ) > 0:

                measurement = (

                    self.measurement_queue.pop()

                )

                return measurement

            time.sleep(0.05)

        log.warning(
            "Vision capture timeout"
        )

        return None

    # -----------------------------------
    # Listener thread
    # -----------------------------------

    def _listener_loop(self):

        self.server = socket.socket(

            socket.AF_INET,
            socket.SOCK_STREAM

        )

        self.server.setsockopt(

            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1

        )

        self.server.bind(

            (
                "0.0.0.0",
                COGNEX_PORT
            )

        )

        self.server.listen(1)

        log.info(
            f"Listening on:{COGNEX_PORT}"
        )

        while self._running:

            try:

                log.info(
                    "Waiting for Cognex connection..."
                )

                client, address = (
                    self.server.accept()
                )

                with self.client_lock:

                    self.client = client

                log.info(
                    f"Cognex connected:{address}"
                )

                buffer = ""

                while self._running:

                    raw = client.recv(
                        4096
                    )

                    if not raw:

                        raise ConnectionError(
                            "Cognex disconnected"
                        )

                    chunk = raw.decode(

                        errors="ignore"

                    )

                    buffer += chunk

                    # -----------------------------------
                    # Extract CRLF packets
                    # -----------------------------------

                    while "\r\n" in buffer:

                        line, buffer = buffer.split(

                            "\r\n",
                            1

                        )

                        line = line.strip()

                        if not line:
                            continue

                        log.info(
                            f"[RAW] {line}"
                        )

                        # Ignore non-json packets
                        if not line.startswith("{"):
                            continue

                        try:

                            packet = json.loads(
                                line
                            )

                        except Exception as e:

                            log.warning(
                                f"JSON parse error:{e}"
                            )

                            continue

                        # -----------------------------------
                        # Parse dimensions
                        # -----------------------------------

                        length = float(

                            packet.get(
                                "length",
                                0
                            )

                        )

                        width = float(

                            packet.get(
                                "width",
                                0
                            )

                        )

                        height = float(

                            packet.get(
                                "height",
                                0
                            )

                        )

                        volume = float(

                            packet.get(
                                "volume",
                                0
                            )

                        )

                        liquid_volume = float(

                            packet.get(
                                "liquid_volume",
                                0
                            )

                        )

                        object_count = int(

                            packet.get(
                                "itemsFound",
                                1
                            )

                        )

                        trigger_id = packet.get(

                            "triggerID",
                            0

                        )

                        # -----------------------------------
                        # Store latest
                        # -----------------------------------

                        self.latest_length = length
                        self.latest_width = width
                        self.latest_height = height
                        self.latest_volume = volume

                        self.latest_count = object_count

                        self.latest_timestamp = (
                            time.time()
                        )

                        # -----------------------------------
                        # Queue payload
                        # -----------------------------------

                        measurement = {

                            "trigger_id":
                            trigger_id,

                            "length":
                            length,

                            "width":
                            width,

                            "height":
                            height,

                            "volume":
                            volume,

                            "liquid_volume":
                            liquid_volume,

                            "object_count":
                            object_count,

                            "timestamp":
                            self.latest_timestamp,

                            # future FTP image support
                            "image_path":
                            None
                        }

                        self.measurement_queue.append(

                            measurement

                        )

                        log.info(

                            f"[Vision] "
                            f"L={length} "
                            f"W={width} "
                            f"H={height} "
                            f"COUNT={object_count} "
                            f"Vol={volume}"

                        )

            except Exception as e:

                log.warning(
                    f"Vision connection error:{e}"
                )

                try:

                    if self.client:
                        self.client.close()

                except:
                    pass

                self.client = None

                time.sleep(1)

    # -----------------------------------
    # Latest accessor
    # -----------------------------------

    def get_latest(self):

        if len(
            self.measurement_queue
        ) == 0:

            return None

        return self.measurement_queue[-1]


# -----------------------------------
# Entry point
# -----------------------------------

def main(args=None):

    logging.basicConfig(
        level=logging.INFO,
        format='[%(name)s][%(levelname)s] %(message)s'
    )

    node = VisionNode()

    try:

        node.start()

        while True:

            time.sleep(
                1
            )

    except KeyboardInterrupt:

        pass

    finally:

        node.stop()


if __name__ == "__main__":

    main()