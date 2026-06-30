"""
DWS+ ROS2 Message Definitions (Pure-Python Simulation Layer)
============================================================
In real ROS2 these live in msg/*.msg and srv/*.srv files compiled by
rosidl. Here they are Python dataclasses so the simulation runs
without any ROS2 installation.

Every field matches the handoff doc exactly.
"""

import time
from dataclasses import dataclass, field
from typing import Optional


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# /dws/scale  (Scale.msg)
# ---------------------------------------------------------------------------
@dataclass
class ScaleMsg:
    weight: float = 0.0        # kg, always positive
    unit: str = "kg"
    timestamp: float = field(default_factory=_now)
    stable: bool = False       # True = ST flag from CAS protocol


# ---------------------------------------------------------------------------
# /dws/vision  (Dimensions.msg)
# ---------------------------------------------------------------------------
@dataclass
class DimensionsMsg:
    length: float = 0.0        # mm
    width: float = 0.0         # mm
    height: float = 0.0        # mm
    platform_height: float = 0.0   # mm, subtracted before publishing
    confidence: float = 1.0    # 0-1, added for Phase 2 RealSense
    frame_id: str = "camera_optical"
    timestamp: float = field(default_factory=_now)


# ---------------------------------------------------------------------------
# /dws/barcode  (std_msgs/String equivalent)
# ---------------------------------------------------------------------------
@dataclass
class BarcodeMsg:
    data: str = ""             # raw AWB / tracking number string
    timestamp: float = field(default_factory=_now)


# ---------------------------------------------------------------------------
# /dws/transaction  (Transaction.msg)
# ---------------------------------------------------------------------------
@dataclass
class TransactionMsg:
    awb: str = ""
    tx_id: str = ""            # UUID generated per barcode scan
    length: float = 0.0        # mm
    width: float = 0.0         # mm
    height: float = 0.0        # mm
    weight: float = 0.0        # kg (actual)
    object_count: int = 1      # number of parcels detected
    volumetric_weight: float = 0.0   # kg (computed)
    chargeable_weight: float = 0.0   # max(actual, volumetric)
    timestamp: float = field(default_factory=_now)
    sync_status: int = 0       # 0=unsynced, 1=synced
    station_id: str = "STATION_01"
    image_url: str = ""
    dim_factor: float = 5000.0
    pointcloud_url: str = ""   # path to saved .ply point cloud (perception_engine v7), "" if unavailable
    latency_ms: float = 0.0    # barcode-to-save latency in milliseconds
    operator: str = ""         # operator ID (future: from settings page)
    confidence: float = 1.0    # measurement confidence 0–1 from perception engine


# ---------------------------------------------------------------------------
# SaveTransaction service (SaveTransaction.srv)
# ---------------------------------------------------------------------------
@dataclass
class SaveTransactionRequest:
    transaction: TransactionMsg = field(default_factory=TransactionMsg)


@dataclass
class SaveTransactionResponse:
    success: bool = False
    message: str = ""
    row_id: int = -1