"""
DWS+ Simulation Bus
===================
Replaces rclpy pub/sub + service calls with a pure-Python in-process
message bus. Design goals:
  - Thread-safe: any thread may publish or subscribe
  - Typed: subscribers receive the exact message class they expect
  - Service calls: synchronous request/response pattern
  - Zero external deps: uses only threading + queue from stdlib

This lets the entire ROS2 architecture run on any Python 3.8+ system
without ROS2 installed. When you port to real ROS2, you replace this
file with rclpy and the node classes call self.create_publisher /
self.create_subscription / self.create_client exactly as documented.
"""

import threading
import queue
import logging
import time
from typing import Callable, Dict, List, Any, Optional, Tuple

log = logging.getLogger("bus")


class SimBus:
    """
    Central publish-subscribe + service bus.
    One global instance is shared by all nodes.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # topic_name -> list of (callback, queue_depth)
        self._subscribers: Dict[str, List[Callable]] = {}
        # service_name -> handler callable
        self._services: Dict[str, Callable] = {}
        # per-subscriber thread pools keep callbacks off the publisher thread
        self._cb_queues: Dict[str, queue.Queue] = {}   # cb_id -> queue
        self._cb_threads: Dict[str, threading.Thread] = {}
        self._running = True

    # ------------------------------------------------------------------
    # Pub / Sub
    # ------------------------------------------------------------------

    def subscribe(self, topic: str, callback: Callable, queue_depth: int = 20) -> str:
        """Register callback on topic. Returns opaque cb_id."""
        cb_id = f"{topic}#{id(callback)}#{time.monotonic_ns()}"
        q = queue.Queue(maxsize=queue_depth)
        t = threading.Thread(
            target=self._dispatch_worker,
            args=(q, callback, topic),
            daemon=True,
            name=f"cb-{cb_id[:30]}"
        )
        t.start()
        with self._lock:
            self._subscribers.setdefault(topic, []).append(cb_id)
            self._cb_queues[cb_id] = q
            self._cb_threads[cb_id] = t
        log.debug(f"Subscribed {cb_id[:40]} -> {topic}")
        return cb_id

    def publish(self, topic: str, msg: Any) -> int:
        """Publish msg to all subscribers. Returns number of queued deliveries."""
        with self._lock:
            cb_ids = list(self._subscribers.get(topic, []))
        delivered = 0
        for cb_id in cb_ids:
            q = self._cb_queues.get(cb_id)
            if q is not None:
                try:
                    q.put_nowait(msg)
                    delivered += 1
                except queue.Full:
                    log.warning(f"Topic {topic}: subscriber queue full, dropping message")
        return delivered

    def _dispatch_worker(self, q: queue.Queue, callback: Callable, topic: str):
        while self._running:
            try:
                msg = q.get(timeout=0.5)
                try:
                    callback(msg)
                except Exception as e:
                    log.error(f"Callback error on {topic}: {e}", exc_info=True)
            except queue.Empty:
                pass

    # ------------------------------------------------------------------
    # Services (synchronous request/response)
    # ------------------------------------------------------------------

    def register_service(self, name: str, handler: Callable):
        with self._lock:
            self._services[name] = handler
        log.debug(f"Service registered: {name}")

    def call_service(self, name: str, request: Any, timeout: float = 5.0) -> Optional[Any]:
        """Synchronous service call. Returns response or None on timeout."""
        result_holder = [None]
        done = threading.Event()

        def _call():
            with self._lock:
                handler = self._services.get(name)
            if handler is None:
                log.error(f"Service not found: {name}")
                done.set()
                return
            try:
                result_holder[0] = handler(request)
            except Exception as e:
                log.error(f"Service {name} raised: {e}", exc_info=True)
            finally:
                done.set()

        t = threading.Thread(target=_call, daemon=True)
        t.start()
        if not done.wait(timeout):
            log.error(f"Service call to {name} timed out after {timeout}s")
            return None
        return result_holder[0]

    def service_is_ready(self, name: str) -> bool:
        with self._lock:
            return name in self._services

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def shutdown(self):
        self._running = False


# Singleton bus shared across all nodes in the simulation
BUS = SimBus()
