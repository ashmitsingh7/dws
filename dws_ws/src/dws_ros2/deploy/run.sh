#!/bin/bash
# =============================================================================
# DWS System — headless process manager
# =============================================================================
# Replaces start_dws.sh's gnome-terminal windows with background processes
# that work with no display attached (SSH session, systemd, fresh boot).
#
# Manages the same three processes start_dws.sh always managed:
#   1. DWS ROS2 backend   (ros2 run dws_ros2 run_dws)
#   2. REST API server    (uvicorn api_server:app --port 8000)
#   3. HMI HTTP server    (python3 -m http.server 8080 → serves hmi/)
#
# Usage:
#   deploy/run.sh start        start all three in the background, detached
#   deploy/run.sh stop         stop all three cleanly (SIGTERM, then wait)
#   deploy/run.sh restart      stop, then start
#   deploy/run.sh status       show whether each process is alive
#   deploy/run.sh foreground   systemd entry point — see dws.service
#
# PID files: $RUN_DIR/<name>.pid     Logs: $LOG_DIR/<name>.log
# Both directories default under $HOME/.dws but can be overridden with
# DWS_RUN_DIR / DWS_LOG_DIR for container or multi-user setups.
# =============================================================================

set -uo pipefail

# -----------------------------------------------------------------------
# Self-locating paths (same approach as start_dws.sh)
# -----------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../dws_ros2/deploy
PKG_DIR="$(dirname "$SCRIPT_DIR")"                            # .../dws_ros2
WS_DIR="$(dirname "$(dirname "$PKG_DIR")")"                  # .../dws_ws
HMI_DIR="$PKG_DIR/hmi"
CAL_DIR="$PKG_DIR/dws_ros2/realsense_vision/calibration"

RUN_DIR="${DWS_RUN_DIR:-$HOME/.dws/run}"
LOG_DIR="${DWS_LOG_DIR:-$HOME/.dws/logs}"
mkdir -p "$RUN_DIR" "$LOG_DIR"

# Optional overrides; defaults match config/dws_params.yaml api/hmi.static_port
HMI_STATIC_PORT="${DWS_HMI_STATIC_PORT:-8080}"
API_PORT="${DWS_API_PORT:-8000}"

BACKEND_PID_FILE="$RUN_DIR/backend.pid"
API_PID_FILE="$RUN_DIR/api.pid"
HMI_PID_FILE="$RUN_DIR/hmi.pid"

ROS_SETUP="/opt/ros/jazzy/setup.bash"
WS_SETUP="$WS_DIR/install/setup.bash"

# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------
is_running() {
  # $1 = pid file. Returns 0 (true) if the PID it contains is alive.
  local pidfile="$1"
  [ -f "$pidfile" ] || return 1
  local pid
  pid="$(cat "$pidfile" 2>/dev/null)"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

stop_one() {
  # $1 = pid file, $2 = friendly name
  local pidfile="$1" name="$2"
  if is_running "$pidfile"; then
    local pid
    pid="$(cat "$pidfile")"
    echo "  stopping $name (pid $pid)..."
    kill -TERM "$pid" 2>/dev/null
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null
  fi
  rm -f "$pidfile"
}

hardware_checks() {
  echo "[checks] Hardware / environment..."

  python3 -c "
import pyrealsense2 as rs
ctx = rs.context()
devices = list(ctx.query_devices())
if devices:
    print('[OK ] RealSense:', devices[0].get_info(rs.camera_info.name))
else:
    print('[WARN] No RealSense detected - check USB connection')
" 2>/dev/null || echo "[WARN] pyrealsense2 not importable"

  local missing=0
  for f in stable_empty_depth.npy noise_map.npy system_geometry.npy; do
    [ -f "$CAL_DIR/$f" ] || { echo "[WARN] Missing calibration: $f"; missing=1; }
  done
  [ "$missing" -eq 0 ] && echo "[OK ] Calibration files present"

  [ -e /dev/ttyUSB0 ] && echo "[OK ] Scale on /dev/ttyUSB0" \
                      || echo "[WARN] Scale not found on /dev/ttyUSB0 — ScaleNode will retry serial at runtime"

  mkdir -p "$PKG_DIR/captures" "$WS_DIR/captures/pointclouds"
}

build_workspace() {
  echo "[build] colcon build --packages-select dws_ros2"
  ( cd "$WS_DIR" && colcon build --packages-select dws_ros2 2>&1 | tail -20 )
}

source_env() {
  # shellcheck disable=SC1090
  [ -f "$ROS_SETUP" ] && source "$ROS_SETUP"
  # shellcheck disable=SC1090
  [ -f "$WS_SETUP" ] && source "$WS_SETUP"
}

# -----------------------------------------------------------------------
# start: launch all three processes detached, in the background
# -----------------------------------------------------------------------
do_start() {
  if is_running "$BACKEND_PID_FILE"; then
    echo "[skip] DWS backend already running (pid $(cat "$BACKEND_PID_FILE"))"
  else
    build_workspace
    hardware_checks
    source_env

    echo "[start] DWS backend -> $LOG_DIR/backend.log"
    cd "$WS_DIR"
    nohup ros2 run dws_ros2 run_dws >> "$LOG_DIR/backend.log" 2>&1 &
    echo $! > "$BACKEND_PID_FILE"
    disown
  fi

  if is_running "$API_PID_FILE"; then
    echo "[skip] REST API already running (pid $(cat "$API_PID_FILE"))"
  else
    echo "[start] REST API (port $API_PORT) -> $LOG_DIR/api.log"
    cd "$PKG_DIR"
    nohup uvicorn api_server:app --host 0.0.0.0 --port "$API_PORT" \
      >> "$LOG_DIR/api.log" 2>&1 &
    echo $! > "$API_PID_FILE"
    disown
  fi

  if is_running "$HMI_PID_FILE"; then
    echo "[skip] HMI server already running (pid $(cat "$HMI_PID_FILE"))"
  else
    echo "[start] HMI HTTP server (port $HMI_STATIC_PORT) -> $LOG_DIR/hmi.log"
    cd "$HMI_DIR"
    nohup python3 -m http.server "$HMI_STATIC_PORT" >> "$LOG_DIR/hmi.log" 2>&1 &
    echo $! > "$HMI_PID_FILE"
    disown
  fi

  echo ""
  echo "DWS running headless."
  echo "  HMI (static):  http://localhost:$HMI_STATIC_PORT"
  echo "  REST API:      http://localhost:$API_PORT  (docs: /docs)"
  echo "  MJPEG/HMI app: http://localhost:8090/video_feed  (from run_dws backend)"
  echo "  WebSocket:     ws://localhost:8765"
  echo "Logs: $LOG_DIR    PIDs: $RUN_DIR"
}

# -----------------------------------------------------------------------
# foreground: systemd entry point (Type=simple)
# -----------------------------------------------------------------------
# Starts API + HMI detached (same as `start`), then execs into the ROS2
# backend so systemd's MainPID is the actual backend process. run_dws.py
# already converts SIGTERM into a graceful KeyboardInterrupt shutdown
# (see run_dws.py), so systemctl stop triggers a clean shutdown rather
# than a hard kill. KillMode=control-group (systemd default) tears down
# the API/HMI children in the same cgroup when the unit stops.
# -----------------------------------------------------------------------
do_foreground() {
  build_workspace
  hardware_checks
  source_env

  echo "[start] REST API (port $API_PORT) -> $LOG_DIR/api.log"
  cd "$PKG_DIR"
  nohup uvicorn api_server:app --host 0.0.0.0 --port "$API_PORT" \
    >> "$LOG_DIR/api.log" 2>&1 &
  echo $! > "$API_PID_FILE"

  echo "[start] HMI HTTP server (port $HMI_STATIC_PORT) -> $LOG_DIR/hmi.log"
  cd "$HMI_DIR"
  nohup python3 -m http.server "$HMI_STATIC_PORT" >> "$LOG_DIR/hmi.log" 2>&1 &
  echo $! > "$HMI_PID_FILE"

  echo "$$" > "$BACKEND_PID_FILE"   # placeholder until exec replaces us
  cd "$WS_DIR"
  exec ros2 run dws_ros2 run_dws
}

# -----------------------------------------------------------------------
# stop / restart / status
# -----------------------------------------------------------------------
do_stop() {
  echo "Stopping DWS..."
  stop_one "$BACKEND_PID_FILE" "DWS backend"
  stop_one "$API_PID_FILE" "REST API"
  stop_one "$HMI_PID_FILE" "HMI server"
  echo "Stopped."
}

do_status() {
  for pair in "$BACKEND_PID_FILE:DWS backend" "$API_PID_FILE:REST API" "$HMI_PID_FILE:HMI server"; do
    pidfile="${pair%%:*}"
    name="${pair##*:}"
    if is_running "$pidfile"; then
      printf '  %-12s RUNNING (pid %s)\n' "$name" "$(cat "$pidfile")"
    else
      printf '  %-12s STOPPED\n' "$name"
    fi
  done
}

do_restart() {
  do_stop
  sleep 1
  do_start
}

case "${1:-}" in
  start)      do_start ;;
  stop)       do_stop ;;
  restart)    do_restart ;;
  status)     do_status ;;
  foreground) do_foreground ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|foreground}"
    exit 1
    ;;
esac
