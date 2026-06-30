"""
sync_worker_node.py
===================
Background cloud sync worker
"""

import threading
import time
import json
import logging
import urllib.request
import urllib.error

log = logging.getLogger("sync_worker_node")

POLL_INTERVAL = 10.0
BATCH_SIZE = 20
MAX_RETRIES = 3
REQUEST_TIMEOUT = 5.0


class SyncWorkerNode:

    def __init__(
        self,
        db_node=None,
        cloud_url="https://api.dws-cloud.example.com/v1/sync",
        poll_interval=POLL_INTERVAL,
        enabled=False,
    ):

        self.db_node = db_node

        self.cloud_url = cloud_url

        self.poll_interval = poll_interval

        self.enabled = enabled

        # Never attempt HTTP sync against the documented placeholder URL.
        if "example.com" in (self.cloud_url or ""):
            self.enabled = False

        self._running = False

        self._thread = None

        self._total_synced = 0
        self._total_failed = 0
        self._sync_cycles = 0


    # ------------------------------------
    # Lifecycle
    # ------------------------------------

    def start(self):

        self._running = True

        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="sync_worker"
        )

        self._thread.start()

        if self.enabled:
            log.info(
                f"SyncWorkerNode started "
                f"| poll_interval={self.poll_interval}s "
                f"| url={self.cloud_url}"
            )
        else:
            log.info(
                "SyncWorkerNode started (cloud sync disabled — "
                "set sync.enabled=true and a real sync.cloud_url in "
                "dws_params.yaml to enable)"
            )


    def stop(self):

        self._running = False

        if self._thread:

            self._thread.join(
                timeout=5
            )

        log.info(
            f"SyncWorkerNode stopped "
            f"| synced={self._total_synced} "
            f"failed={self._total_failed}"
        )


    # ------------------------------------
    # Main loop
    # ------------------------------------

    def _run_loop(self):

        time.sleep(3)

        while self._running:

            try:

                self._sync_cycle()

            except Exception as e:

                log.error(
                    f"[sync] cycle error:{e}"
                )

            time.sleep(
                self.poll_interval
            )
            
    def _sync_cycle(self):

        if not self.enabled:
            return

        if self.db_node is None:
            return

        rows = self.db_node.get_unsynced(
            limit=BATCH_SIZE
        )

        if not rows:
            return

        self._sync_cycles += 1

        synced_ids = []

        for row in rows:

            row_id = row["id"]

            awb = row["awb"]

            success = self._push_to_cloud(
                row
            )

            if success:

                synced_ids.append(
                    row_id
                )

                self._total_synced += 1

                log.info(
                    f"[sync] synced "
                    f"id={row_id} "
                    f"awb={awb}"
                )

            else:

                self._total_failed += 1


        if synced_ids:

            self.db_node.mark_synced(
                synced_ids
            )


    def _push_to_cloud(
        self,
        row
    ):
        """
        Push a single transaction row to the cloud sync endpoint.

        Real HTTP POST via urllib.request (stdlib, no new
        dependency). Retries up to MAX_RETRIES times with
        exponential backoff. Connection errors, timeouts, and
        non-2xx responses are all treated as failure — there is
        no simulated success here. If self.cloud_url is not a
        real, reachable backend (e.g. still the .example.com
        placeholder), this will fail honestly with a real
        connection error instead of faking success.
        """

        payload = {
            key: row[key]
            for key in row.keys()
        }

        body = json.dumps(payload).encode("utf-8")

        last_error = None

        for attempt in range(MAX_RETRIES):

            try:

                request = urllib.request.Request(
                    self.cloud_url,
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )

                with urllib.request.urlopen(
                    request,
                    timeout=REQUEST_TIMEOUT,
                ) as response:

                    status = response.status

                    if 200 <= status < 300:

                        return True

                    last_error = f"HTTP {status}"

            except urllib.error.HTTPError as e:

                last_error = f"HTTP {e.code}"

            except urllib.error.URLError as e:

                last_error = f"connection error: {e.reason}"

            except Exception as e:

                last_error = f"unexpected error: {e}"

            if attempt < MAX_RETRIES - 1:

                delay = min(2 ** attempt, 30)

                log.warning(
                    f"[sync] push attempt {attempt + 1}/"
                    f"{MAX_RETRIES} failed "
                    f"({last_error}), retrying in {delay}s"
                )

                time.sleep(delay)

        log.error(
            f"[sync] push failed after {MAX_RETRIES} attempts: "
            f"{last_error}"
        )

        return False


    def get_stats(self):

        return {

            "total_synced":
            self._total_synced,

            "total_failed":
            self._total_failed,

            "sync_cycles":
            self._sync_cycles
        }


# ------------------------------------
# ROS2 executable entrypoint
# ------------------------------------

def main(args=None):

    logging.basicConfig(
        level=logging.INFO,
        format='[%(name)s][%(levelname)s] %(message)s'
    )

    try:
        from dws_ros2.config_loader import get_config
        sc = get_config()["sync"]
        node = SyncWorkerNode(
            cloud_url=sc["cloud_url"],
            poll_interval=sc["poll_interval_seconds"],
            enabled=sc["enabled"],
        )
    except Exception as e:
        log.warning(f"[CONFIG] Falling back to sync defaults: {e}")
        node = SyncWorkerNode()

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
