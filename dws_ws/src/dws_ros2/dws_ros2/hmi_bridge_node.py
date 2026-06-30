"""
hmi_bridge_node.py
==================

Subscribes to:
- /dws/transaction
- /dws/error
- /dws/calibration_required
- /dws/calibration_warning
- /dws/calibration_started
- /dws/calibration_pass
- /dws/calibration_failed

Streams live DWS updates
to browser HMI via websocket.

Features:
- Loads transaction history from SQLite
- Loads error history from SQLite
- Loads calibration box list from SQLite
- Sends live transaction updates
- Sends live error popup events (with blocking flag)
- Sends live calibration lifecycle events
- Continuously refreshes HMI from DB
- Keeps websocket + DB synchronized
- Serves image captures over HTTP
"""

import threading
import time
import json
import logging
import asyncio
import sqlite3
import websockets
import os

from aiohttp import web
from aiohttp.web_response import StreamResponse
from collections import deque

from dws_ros2.sim_bus import BUS
from dws_ros2.messages import TransactionMsg
from dws_ros2.paths import (
    DB_PATH,
    CAPTURE_DIR,
    POINTCLOUD_DIR,
    HMI_DIR,
)


log = logging.getLogger(
    "hmi_bridge_node"
)

MAX_HISTORY = 500

HTTP_BASE = "http://127.0.0.1:8090"


def _http_base(http_port: int) -> str:
    return f"http://127.0.0.1:{http_port}"


def _path_to_url(fspath: str | None, http_port: int = 8090) -> str | None:
    """
    Convert an absolute filesystem path to an HTTP URL served by the
    aiohttp server on port 8090.

    /home/xxx/dws_ws/src/dws_ros2/captures/foo.jpg
        → http://127.0.0.1:8090/captures/foo.jpg

    /home/xxx/dws_ws/captures/pointclouds/tx_abc.ply
        → http://127.0.0.1:8090/pointclouds/tx_abc.ply

    Falls back to the original string if neither prefix matches so old
    records that already contain an HTTP URL are returned unchanged.
    """
    if not fspath:
        return None

    if fspath.startswith(("http://", "https://")):
        return fspath

    cap_dir  = CAPTURE_DIR.rstrip("/")
    pcd_dir  = POINTCLOUD_DIR.rstrip("/")
    base = _http_base(http_port)

    if fspath.startswith(cap_dir):
        filename = fspath[len(cap_dir):].lstrip("/")
        return f"{base}/captures/{filename}"

    if fspath.startswith(pcd_dir):
        filename = fspath[len(pcd_dir):].lstrip("/")
        return f"{base}/pointclouds/{filename}"

    # Unknown path — return as-is (avoids breaking custom setups)
    return fspath


class HmiBridgeNode:

    def __init__(self, websocket_port=8765, http_port=8090, orchestrator=None):

        self.websocket_port = websocket_port
        self.http_port = http_port
        self.orchestrator = orchestrator
        self._running = False

        self._history = deque(
            maxlen=MAX_HISTORY
        )

        self._history_lock = (
            threading.Lock()
        )

        self._ui_hooks = []

        self._event_count = 0

        self._error_count = 0

        self._ws_loop = None

        self._ws_clients = set()

        self._last_db_count = -1

        self._last_error_count = -1

    # -----------------------------------
    # Lifecycle
    # -----------------------------------

    def start(self):

        self._running = True

        BUS.subscribe(
            "/dws/transaction",
            self._on_transaction
        )

        BUS.subscribe(
            "/dws/error",
            self._on_error
        )

        BUS.subscribe(
            "/dws/calibration_required",
            self._on_calibration_required
        )

        BUS.subscribe(
            "/dws/calibration_warning",
            self._on_calibration_warning
        )

        BUS.subscribe(
            "/dws/calibration_started",
            self._on_calibration_started
        )

        BUS.subscribe(
            "/dws/calibration_pass",
            self._on_calibration_pass
        )

        BUS.subscribe(
            "/dws/calibration_failed",
            self._on_calibration_failed
        )

        BUS.subscribe(
            "/dws/system_state",
            self._on_system_state
        )

        self._ws_loop = (
            asyncio.new_event_loop()
        )

        threading.Thread(
            target=self._start_ws_server,
            daemon=True
        ).start()

        log.info(
            f"HmiBridgeNode started "
            f"(ws={self.websocket_port}, http={self.http_port})"
        )

    def stop(self):

        self._running = False

        log.info(
            f"HmiBridgeNode stopped "
            f"| tx={self._event_count}"
            f" | err={self._error_count}"
        )

    # -----------------------------------
    # SQLite transaction history
    # -----------------------------------

    def get_transactions(self):

        try:

            conn = sqlite3.connect(
                DB_PATH
            )

            conn.row_factory = sqlite3.Row

            cursor = conn.cursor()

            cursor.execute("""

                SELECT *
                FROM transactions
                ORDER BY id DESC
                LIMIT 500

            """)

            rows = []

            for row in cursor.fetchall():

                rows.append({

                    "id": row["id"],

                    "awb": row["awb"],
                    "tx_id": row["tx_id"] if "tx_id" in row.keys() else None,
                    "l": float(row["length"]),
                    "w": float(row["width"]),
                    "h": float(row["height"]),
                    "wt": float(row["weight"]),
                    "vol_wt": float(row["vol_weight"]),
                    "charge_wt": float(row["charge_weight"]),
                    "station": row["station_id"],
                    "ts": row["timestamp"],
                    "ts_str": row["created_at"],
                    "sync": row["sync_status"],
                    "fsm": "COMPLETE",

                    "object_count":
                    row["object_count"]
                    if "object_count" in row.keys()
                    else 1,

                    "image_path":
                    _path_to_url(
                        row["image_url"]
                        if "image_url" in row.keys()
                        else None,
                        self.http_port,
                    ),

                    "pointcloud_path":
                    _path_to_url(
                        row["pointcloud_url"]
                        if "pointcloud_url" in row.keys()
                        else None,
                        self.http_port,
                    ),

                    "confidence":
                    float(row["confidence"])
                    if "confidence" in row.keys()
                    else 1.0

                })

            conn.close()

            return rows

        except Exception as e:

            log.error(
                f"DB history read error: {e}"
            )

            return []

    # -----------------------------------
    # SQLite error history
    # -----------------------------------

    def get_errors(self):

        try:

            conn = sqlite3.connect(
                DB_PATH
            )

            conn.row_factory = sqlite3.Row

            cursor = conn.cursor()

            cursor.execute("""

                SELECT *
                FROM errors
                ORDER BY id DESC
                LIMIT 500

            """)

            rows = []

            for row in cursor.fetchall():

                rows.append({

                    "id": row["id"],

                    "error_type":
                    row["error_type"],

                    "awb":
                    row["awb"],

                    "message":
                    row["message"],

                    "created_at":
                    row["created_at"],

                    "timestamp":
                    row["timestamp"],

                    "image_path":
                    _path_to_url(
                        row["image_url"]
                        if "image_url" in row.keys()
                        else None,
                        self.http_port,
                    ),

                    "severity":
                    row["severity"]
                    if "severity" in row.keys()
                    else "MEDIUM",

                    "component":
                    row["component"]
                    if "component" in row.keys()
                    else "processing"

                })

            conn.close()

            return rows

        except Exception as e:

            log.error(
                f"DB error history read error: {e}"
            )

            return []

    # -----------------------------------
    # SQLite calibration boxes
    # -----------------------------------

    def get_calibration_boxes(self):

        try:

            conn = sqlite3.connect(
                DB_PATH
            )

            conn.row_factory = sqlite3.Row

            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT *
                FROM calibration_boxes
                ORDER BY awb
                """
            )

            rows = [

                dict(r)

                for r in cursor.fetchall()

            ]

            conn.close()

            return rows

        except Exception as e:

            log.error(
                f"Calibration box read error:{e}"
            )

            return []

    # -----------------------------------
    # Row counts
    # -----------------------------------

    def get_row_count(self):

        try:

            conn = sqlite3.connect(
                DB_PATH
            )

            cursor = conn.cursor()

            cursor.execute(

                "SELECT COUNT(*) FROM transactions"

            )

            count = cursor.fetchone()[0]

            conn.close()

            return count

        except:

            return 0

    def get_error_count(self):

        try:

            conn = sqlite3.connect(
                DB_PATH
            )

            cursor = conn.cursor()

            cursor.execute(

                "SELECT COUNT(*) FROM errors"

            )

            count = cursor.fetchone()[0]

            conn.close()

            return count

        except:

            return 0

    # -----------------------------------
    # Websocket + HTTP server
    # -----------------------------------

    # ============================================================
    # MJPEG GENERATOR (async)
    # ============================================================

    async def mjpeg_generator_async(self, response):

        while True:

            try:

                frame = (
                    self.orchestrator
                    .get_live_jpeg()
                ) if self.orchestrator else None

                if frame is None:

                    await asyncio.sleep(0.06)
                    continue

                # -----------------------------------
                # Save latest frame to disk so
                # /captures/latest.jpg is always
                # fresh for the HMI static image
                # -----------------------------------
                try:

                    capture_dir = CAPTURE_DIR

                    os.makedirs(
                        capture_dir,
                        exist_ok=True
                    )

                    capture_path = os.path.join(
                        capture_dir,
                        "latest.jpg"
                    )

                    with open(capture_path, "wb") as f:
                        f.write(frame)

                except Exception as _ce:

                    log.warning(
                        f"[hmi] capture save error: {_ce}"
                    )

                chunk = (
                    b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n\r\n'
                    + frame +
                    b'\r\n'
                )

                await response.write(chunk)

                await asyncio.sleep(0.033)

            except asyncio.CancelledError:

                break

            except Exception as e:

                log.warning(
                    f"MJPEG stream error: {e}"
                )

                await asyncio.sleep(0.1)

    def _start_ws_server(self):

        asyncio.set_event_loop(
            self._ws_loop
        )

        async def start():

            # -----------------------------------
            # WebSocket Server
            # -----------------------------------
            server = await websockets.serve(

                self._client_handler,

                "localhost",

                self.websocket_port

            )

            log.info(

                f"[hmi] WebSocket server active "
                f"on ws://localhost:{self.websocket_port}"

            )

            # -----------------------------------
            # Ensure capture directory exists
            # -----------------------------------
            if not os.path.exists(
                CAPTURE_DIR
            ):

                os.makedirs(
                    CAPTURE_DIR,
                    exist_ok=True
                )

            # -----------------------------------
            # Static HTTP image server
            # -----------------------------------

            async def cors_middleware(app, handler):

                async def middleware(request):

                    if request.method == "OPTIONS":

                        return web.Response(

                            headers={
                                "Access-Control-Allow-Origin": "*",
                                "Access-Control-Allow-Headers": "*",
                                "Access-Control-Allow-Methods":
                                "GET, POST, OPTIONS",
                            }

                        )

                    response = await handler(request)

                    response.headers[
                        "Access-Control-Allow-Origin"
                    ] = "*"

                    return response

                return middleware

            app = web.Application(
                middlewares=[cors_middleware]
            )

            # ============================================================
            # HMI FRONTEND ROUTE
            # Serves hmi/index.html as the root page so opening
            # http://localhost:8090 loads the operator terminal
            # directly, instead of 404'ing.
            # ============================================================

            async def serve_index(request):

                index_path = os.path.join(
                    HMI_DIR,
                    "index.html"
                )

                if not os.path.isfile(index_path):

                    log.error(
                        f"[hmi] index.html not found "
                        f"at {index_path}"
                    )

                    return web.Response(
                        status=404,
                        text="HMI index.html not found"
                    )

                return web.FileResponse(

                    index_path,

                    headers={
                        "Cache-Control": "no-cache",
                    }

                )

            app.router.add_get(
                "/",
                serve_index
            )

            app.router.add_get(
                "/index.html",
                serve_index
            )

            # ============================================================
            # MJPEG VIDEO FEED ROUTE
            # ============================================================

            async def video_feed(request):

                response = StreamResponse(

                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Headers": "*",
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
                    }

                )

                response.content_type = (
                    "multipart/x-mixed-replace;"
                    " boundary=frame"
                )

                await response.prepare(request)

                await self.mjpeg_generator_async(
                    response
                )

                return response

            # ============================================================
            # VISION STATUS ROUTE
            # ============================================================

            async def vision_status(request):

                if (
                    self.orchestrator is None
                    or
                    self.orchestrator.vision_node is None
                ):

                    return web.json_response(
                        {"online": False}
                    )

                latest = getattr(
                    self.orchestrator.vision_node,
                    "latest_result",
                    None
                )

                if latest is None:

                    return web.json_response(
                        {"online": False}
                    )

                return web.json_response({

                    "online": True,

                    "locked":
                        latest.get("locked", False),

                    "valid":
                        latest.get("valid", False),

                    "human_detected":
                        latest.get(
                            "human_detected",
                            False
                        ),

                    "confidence":
                        latest.get(
                            "confidence",
                            0
                        ),

                    "length_mm":
                        latest.get(
                            "length_mm",
                            0
                        ),

                    "breadth_mm":
                        latest.get(
                            "breadth_mm",
                            0
                        ),

                    "height_mm":
                        latest.get(
                            "height_mm",
                            0
                        )

                })

            # ============================================================
            # EXPLICIT CAPTURE FILE SERVE ROUTE  (fixes 403)
            # ============================================================

            async def serve_capture(request):

                filename = request.match_info["filename"]

                filepath = os.path.join(
                    CAPTURE_DIR,
                    filename
                )

                if not os.path.isfile(filepath):

                    return web.Response(status=404)

                return web.FileResponse(

                    filepath,

                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Cache-Control": "no-cache",
                    }

                )

            app.router.add_get(
                "/video_feed",
                video_feed
            )

            app.router.add_get(
                "/vision_status",
                vision_status
            )

            # Explicit route takes priority — fixes 403 on /captures
            app.router.add_get(
                "/captures/{filename}",
                serve_capture
            )

            # Static fallback with symlink + index support
            app.router.add_static(
                "/captures",
                CAPTURE_DIR,
                show_index=True,
                follow_symlinks=True,
                append_version=False,
            )

            # ----------------------------------------------------------
            # POINTCLOUD SERVE ROUTE
            # Serves .ply files from ~/dws_ws/captures/pointclouds
            # as binary downloads; also exposes a static index.
            # ----------------------------------------------------------

            os.makedirs(POINTCLOUD_DIR, exist_ok=True)

            async def serve_pointcloud(request):

                filename = request.match_info["filename"]

                # Sanitise — no path traversal
                if (
                    ".." in filename
                    or "/" in filename
                    or "\\" in filename
                ):

                    return web.Response(status=400)

                filepath = os.path.join(
                    POINTCLOUD_DIR,
                    filename
                )

                if not os.path.isfile(filepath):

                    return web.Response(status=404)

                return web.FileResponse(

                    filepath,

                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Content-Disposition":
                            f'attachment; filename="{filename}"',
                        "Cache-Control": "no-cache",
                    }

                )

            app.router.add_get(
                "/pointclouds/{filename}",
                serve_pointcloud
            )

            app.router.add_static(
                "/pointclouds",
                POINTCLOUD_DIR,
                show_index=True,
                follow_symlinks=True,
                append_version=False,
            )

            # ----------------------------------------------------------
            # HMI STATIC ASSETS
            # index.html is currently self-contained (inline CSS/JS),
            # but this mount covers any sibling assets added later
            # without needing another server change.
            # ----------------------------------------------------------

            if os.path.isdir(HMI_DIR):

                app.router.add_static(
                    "/hmi",
                    HMI_DIR,
                    show_index=False,
                    follow_symlinks=True,
                    append_version=False,
                )

            else:

                log.error(
                    f"[hmi] HMI directory not found "
                    f"at {HMI_DIR} — root page will 404"
                )

            runner = web.AppRunner(
                app
            )

            await runner.setup()

            site = web.TCPSite(

                runner,

                "localhost",

                self.http_port

            )

            await site.start()

            log.info(

                f"[hmi] Static image server active "
                f"at http://localhost:{self.http_port}/captures"

            )

            # -----------------------------------
            # Background DB sync
            # -----------------------------------
            asyncio.create_task(
                self._db_history_task()
            )

            return server

        self._ws_loop.run_until_complete(
            start()
        )

        self._ws_loop.run_forever()

    async def _client_handler(
        self,
        websocket,
        path=None
    ):

        self._ws_clients.add(
            websocket
        )

        log.info(
            f"[hmi] Client connected "
            f"({len(self._ws_clients)})"
        )

        try:

            history = self.get_transactions()

            await websocket.send(

                json.dumps({

                    "type": "history",

                    "data": history

                })

            )

            errors = self.get_errors()

            await websocket.send(

                json.dumps({

                    "type": "error_history",

                    "data": errors

                })

            )

            boxes = self.get_calibration_boxes()

            await websocket.send(

                json.dumps({

                    "type": "calibration_boxes",

                    "data": boxes

                })

            )

            async for raw in websocket:

                try:

                    msg = json.loads(raw)

                except Exception:
                    continue

                event = msg.get("event")

                if (
                    event == "start_auto_calibration"
                    and
                    self.orchestrator is not None
                ):

                    log.info(
                        "[hmi] Auto calibration triggered"
                    )

                    import threading

                    threading.Thread(

                        target=(
                            self.orchestrator
                            .start_auto_calibration
                        ),

                        daemon=True

                    ).start()

        finally:

            self._ws_clients.discard(
                websocket
            )

    # -----------------------------------
    # Continuous DB sync
    # -----------------------------------

    async def _db_history_task(self):

        while self._running:

            try:

                # transaction sync
                count = self.get_row_count()

                if count != self._last_db_count:

                    history = self.get_transactions()

                    payload = {

                        "type": "history",

                        "data": history

                    }

                    await self._broadcast_raw(
                        payload
                    )

                    self._last_db_count = count

                    log.info(

                        f"[hmi] synced "
                        f"{count} TX rows"

                    )

                # error sync
                err_count = self.get_error_count()

                if err_count != self._last_error_count:

                    errors = self.get_errors()

                    payload = {

                        "type": "error_history",

                        "data": errors

                    }

                    await self._broadcast_raw(
                        payload
                    )

                    self._last_error_count = err_count

                    log.info(

                        f"[hmi] synced "
                        f"{err_count} ERROR rows"

                    )

            except Exception as e:

                log.error(

                    f"[hmi] DB sync: {e}"

                )

            await asyncio.sleep(
                1
            )

    # -----------------------------------
    # Broadcast helpers
    # -----------------------------------

    async def _broadcast_raw(
        self,
        payload
    ):

        if not self._ws_clients:
            return

        message = json.dumps(
            payload
        )

        dead = []

        for client in self._ws_clients:

            try:

                await client.send(
                    message
                )

            except:

                dead.append(
                    client
                )

        for c in dead:

            self._ws_clients.discard(
                c
            )

    async def _broadcast(
        self,
        payload
    ):

        await self._broadcast_raw({

            "type": "live",

            **payload

        })

    # -----------------------------------
    # Transaction callback
    # -----------------------------------

    def _on_transaction(
        self,
        msg
    ):

        payload = self._to_dict(
            msg
        )

        self._event_count += 1

        log.info(

            f"[hmi] "
            f"TX #{self._event_count}"
            f" | AWB={msg.awb}"

        )

        if self._ws_loop:

            asyncio.run_coroutine_threadsafe(

                self._broadcast(
                    payload
                ),

                self._ws_loop

            )

    # -----------------------------------
    # Error callback
    # -----------------------------------

    def _on_error(
        self,
        payload
    ):

        self._error_count += 1

        blocking = (

            payload.get(
                "error_type"
            )

            in

            [

                "CALIBRATION_REQUIRED",

                "CALIBRATION_FAILED"

            ]

        )

        error_packet = {

            "type": "error",

            "error_type":
            payload.get(
                "error_type",
                "UNKNOWN"
            ),

            "awb":
            payload.get(
                "awb",
                "UNKNOWN"
            ),

            "message":
            payload.get(
                "message",
                "System Error"
            ),

            "timestamp":
            payload.get(
                "timestamp",
                time.time()
            ),

            "blocking":
            blocking,

            "error_count":
            self._error_count,

            "vision_reason":
            payload.get("reason")

        }

        log.warning(

            f"[hmi] ERROR "
            f"| {error_packet['error_type']} "
            f"| blocking={blocking}"

        )

        if self._ws_loop:

            asyncio.run_coroutine_threadsafe(

                self._broadcast_raw(
                    error_packet
                ),

                self._ws_loop

            )

    # -----------------------------------
    # Calibration event callbacks
    # -----------------------------------

    def _on_calibration_required(
        self,
        payload
    ):

        packet = {

            "type":
            "calibration_required",

            **payload

        }

        if self._ws_loop:

            asyncio.run_coroutine_threadsafe(

                self._broadcast_raw(
                    packet
                ),

                self._ws_loop

            )

    def _on_calibration_warning(
        self,
        payload
    ):

        packet = {

            "type":
            "calibration_warning",

            **payload

        }

        if self._ws_loop:

            asyncio.run_coroutine_threadsafe(

                self._broadcast_raw(
                    packet
                ),

                self._ws_loop

            )

    def _on_calibration_started(
        self,
        payload
    ):

        packet = {

            "type":
            "calibration_started",

            **payload

        }

        if self._ws_loop:

            asyncio.run_coroutine_threadsafe(

                self._broadcast_raw(
                    packet
                ),

                self._ws_loop

            )

    def _on_calibration_pass(
        self,
        payload
    ):

        packet = {

            "type":
            "calibration_pass",

            **payload

        }

        if self._ws_loop:

            asyncio.run_coroutine_threadsafe(

                self._broadcast_raw(
                    packet
                ),

                self._ws_loop

            )

    def _on_system_state(
        self,
        msg
    ):

        payload = {

            "type": "system_state",

            "data": msg
        }

        if self._ws_loop:

            asyncio.run_coroutine_threadsafe(

                self._broadcast_raw(
                    payload
                ),

                self._ws_loop

            )

    def _on_calibration_failed(
        self,
        payload
    ):

        packet = {

            "type":
            "calibration_failed",

            **payload

        }

        if self._ws_loop:

            asyncio.run_coroutine_threadsafe(

                self._broadcast_raw(
                    packet
                ),

                self._ws_loop

            )

    # -----------------------------------
    # Formatter
    # -----------------------------------

    def _to_dict(self, msg):

        return {

            "awb": msg.awb,
            "tx_id": getattr(msg, "tx_id", None),
            "l": float(msg.length),
            "w": float(msg.width),
            "h": float(msg.height),
            "wt": float(msg.weight),

            "object_count":
            getattr(
                msg,
                "object_count",
                1
            ),

            "vol_wt": float(
                msg.volumetric_weight
            ),

            "charge_wt": float(
                msg.chargeable_weight
            ),

            "station":
            msg.station_id,

            "ts":
            msg.timestamp,

            "ts_str":
            time.strftime(

                "%Y-%m-%d %H:%M:%S",

                time.localtime(
                    msg.timestamp
                )

            ),

            "sync":
            msg.sync_status,

            "image_path":
            _path_to_url(getattr(msg, "image_url", None), self.http_port),

            "pointcloud_path":
            _path_to_url(getattr(msg, "pointcloud_url", None), self.http_port),

            "fsm": "COMPLETE"

        }


def main(args=None):

    logging.basicConfig(

        level=logging.INFO,

        format=
        '[%(name)s][%(levelname)s] %(message)s'

    )

    node = HmiBridgeNode()

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