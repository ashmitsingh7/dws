#!/bin/bash
# =============================================================================
# DWS System Startup Script
# =============================================================================
# Starts (in separate terminals):
#   1. DWS ROS2 backend  (ros2 run dws_ros2 run_dws)
#   2. HMI HTTP server   (python3 -m http.server 8080  → serves hmi/)
#   3. REST API server   (uvicorn api_server:app --port 8000)
#
# The aiohttp MJPEG / WebSocket / capture-serve server (port 8090 / 8765)
# is started automatically by hmi_bridge_node inside the backend.
# =============================================================================

set -euo pipefail

# Self-locating: this script lives at $WS_DIR/src/dws_ros2/start_dws.sh,
# so derive WS_DIR from the script's own path instead of assuming the
# workspace lives at $HOME/dws_ws (breaks under any other checkout
# location, container, or username).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$SCRIPT_DIR"
WS_DIR="$(dirname "$(dirname "$PKG_DIR")")"
HMI_DIR="$PKG_DIR/hmi"
CAL_DIR="$PKG_DIR/dws_ros2/realsense_vision/calibration"

echo "===================================="
echo " DWS System Startup"
echo "===================================="
echo ""

# -----------------------------------------------------------------------
# 0. Kill any stale processes from a previous run
# -----------------------------------------------------------------------
echo "[0/5] Stopping stale DWS processes..."
pkill -f "run_dws" 2>/dev/null || true
pkill -f "uvicorn.*api_server" 2>/dev/null || true
pkill -f "http.server 8080" 2>/dev/null || true
sleep 1

# -----------------------------------------------------------------------
# 1. Build
# -----------------------------------------------------------------------
echo "[1/5] Building ROS2 package..."
cd "$WS_DIR"
colcon build --packages-select dws_ros2 2>&1 | tail -5

if [ $? -ne 0 ]; then
  echo "[FAIL] colcon build failed"
  exit 1
fi

# ROS setup scripts are not compatible with 'set -u'
set +u
source /opt/ros/jazzy/setup.bash
source "$WS_DIR/install/setup.bash"
set -u

# -----------------------------------------------------------------------
# 2. Hardware checks (non-fatal — system handles missing HW gracefully)
# -----------------------------------------------------------------------
echo ""
echo "[2/5] Hardware checks..."

# Camera
python3 -c "
import pyrealsense2 as rs
ctx = rs.context()
devices = list(ctx.query_devices())
if devices:
    print('[OK ] RealSense:', devices[0].get_info(rs.camera_info.name))
else:
    print('[WARN] No RealSense detected — check USB connection')
" 2>/dev/null || echo "[WARN] pyrealsense2 not importable"

# Calibration
MISSING=0
for f in stable_empty_depth.npy noise_map.npy system_geometry.npy; do
  [ -f "$CAL_DIR/$f" ] || { echo "[WARN] Missing calibration: $f"; MISSING=1; }
done
[ $MISSING -eq 0 ] && echo "[OK ] Calibration files present"

# Scale
[ -e /dev/ttyUSB0 ] && echo "[OK ] Scale on /dev/ttyUSB0" \
                    || echo "[WARN] Scale not found on /dev/ttyUSB0 — ScaleNode will retry serial at runtime"

# Ensure output directories exist
mkdir -p "$PKG_DIR/captures"
mkdir -p "$WS_DIR/captures/pointclouds"

# -----------------------------------------------------------------------
# 3. DWS backend
# -----------------------------------------------------------------------
echo ""
echo "[3/5] Launching DWS backend..."

gnome-terminal --title="DWS Backend" -- bash -c "
  set +u
  source /opt/ros/jazzy/setup.bash
  source '$WS_DIR/install/setup.bash'
  set -u
  cd '$WS_DIR'
  echo '--- DWS Backend ---'
  ros2 run dws_ros2 run_dws
  exec bash
" &

sleep 5

# -----------------------------------------------------------------------
# 4. REST API server
# -----------------------------------------------------------------------
echo "[4/5] Launching REST API (port 8000)..."

gnome-terminal --title="DWS REST API" -- bash -c "
  cd '$PKG_DIR'
  echo '--- DWS REST API ---'
  uvicorn api_server:app --host 0.0.0.0 --port 8000 --reload
  exec bash
" &

sleep 2

# -----------------------------------------------------------------------
# 5. HMI HTTP server
# -----------------------------------------------------------------------
echo "[5/5] Launching HMI server (port 8080)..."

gnome-terminal --title="DWS HMI Server" -- bash -c "
  cd '$HMI_DIR'
  echo '--- DWS HMI HTTP Server ---'
  python3 -m http.server 8080
  exec bash
" &

sleep 2

# -----------------------------------------------------------------------
# Open browser
# -----------------------------------------------------------------------
xdg-open "http://localhost:8080/index.html" >/dev/null 2>&1 || true

echo ""
echo "===================================="
echo " DWS SYSTEM RUNNING"
echo "===================================="
echo ""
echo "  HMI:             http://localhost:8080"
echo "  REST API:        http://localhost:8000"
echo "  API Docs:        http://localhost:8000/docs"
echo "  MJPEG Feed:      http://localhost:8090/video_feed"
echo "  WebSocket:       ws://localhost:8765"
echo "  Captures:        http://localhost:8090/captures"
echo "  Point Clouds:    http://localhost:8090/pointclouds"
echo ""
echo "  Boot sequence:   BOOTING → CHECKING_CAMERA"
echo "                   → CHECKING_CALIBRATION → CHECKING_SCALE → IDLE"
echo ""
echo "  If calibration missing: BOOTING → CALIBRATION_REQUIRED"
echo "  Run calibration from HMI then restart."
echo ""
