#!/bin/bash
# =============================================================================
# DWS System — Installation Verifier
# =============================================================================
# Checks every subsystem and prints PASS / FAIL / WARNING for each.
# Can be run as the operator user (no sudo required).
#
# Usage:
#   bash deploy/verify_install.sh [--no-hardware] [--json] [--quiet]
#
#   --no-hardware   Skip live hardware probes (RealSense, scale, barcode)
#   --json          Emit a machine-readable JSON summary to stdout after checks
#   --quiet         Only print FAIL / WARNING lines (suppress PASS)
# =============================================================================

set -uo pipefail

# Ensure USER is set even in sudo/non-login environments
USER="${USER:-$(id -un)}"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(dirname "$SCRIPT_DIR")"
WS_DIR="$(dirname "$(dirname "$PKG_DIR")")"
REPO_ROOT="$(dirname "$WS_DIR")"

VENV_DIR="$WS_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python3"
RUNTIME_BASE="$REPO_ROOT/dws_runtime"
CONFIG_FILE="$PKG_DIR/config/dws_params.yaml"
WS_SETUP="$WS_DIR/install/setup.bash"

API_PORT="${DWS_API_PORT:-8000}"
HMI_STATIC_PORT="${DWS_HMI_STATIC_PORT:-8080}"
WS_PORT="${DWS_WS_PORT:-8765}"

_UBUNTU_RELEASE=$(lsb_release -rs 2>/dev/null || echo "unknown")
case "$_UBUNTU_RELEASE" in
  24.04) ROS_DISTRO="jazzy"  ;;
  22.04) ROS_DISTRO="humble" ;;
  *)     ROS_DISTRO="jazzy"  ;;
esac

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
OPT_NO_HARDWARE=0
OPT_JSON=0
OPT_QUIET=0
for arg in "$@"; do
  case "$arg" in
    --no-hardware) OPT_NO_HARDWARE=1 ;;
    --json)        OPT_JSON=1 ;;
    --quiet)       OPT_QUIET=1 ;;
    -h|--help)
      grep '^#' "$0" | head -14 | sed 's/^# \{0,1\}//'
      exit 0
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Colour and result tracking
# ---------------------------------------------------------------------------
CLR_PASS='\033[0;32m'
CLR_FAIL='\033[0;31m'
CLR_WARN='\033[0;33m'
CLR_BOLD='\033[1m'
CLR_RST='\033[0m'

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

# JSON accumulator
JSON_RESULTS=()

_result() {
  # _result SUBSYSTEM STATUS [detail]
  local subsystem="$1"
  local status="$2"
  local detail="${3:-}"

  case "$status" in
    PASS)
      PASS_COUNT=$((PASS_COUNT + 1))
      local colour="$CLR_PASS"
      ;;
    FAIL)
      FAIL_COUNT=$((FAIL_COUNT + 1))
      local colour="$CLR_FAIL"
      ;;
    WARNING)
      WARN_COUNT=$((WARN_COUNT + 1))
      local colour="$CLR_WARN"
      ;;
    *)
      local colour="$CLR_RST"
      ;;
  esac

  local label
  printf -v label "%-40s" "$subsystem"

  if [ "$OPT_QUIET" -eq 0 ] || [ "$status" != "PASS" ]; then
    printf "${colour}[%-7s]${CLR_RST} ${CLR_BOLD}%s${CLR_RST}" "$status" "$label"
    if [ -n "$detail" ]; then
      printf "  %s" "$detail"
    fi
    printf "\n"
  fi

  # Accumulate for JSON
  # Escape quotes in detail
  local safe_detail
  safe_detail="${detail//\"/\'}"
  JSON_RESULTS+=("{\"subsystem\":\"$subsystem\",\"status\":\"$status\",\"detail\":\"$safe_detail\"}")
}

section() {
  printf "\n${CLR_BOLD}── %s${CLR_RST}\n" "$*"
}

# ---------------------------------------------------------------------------
# START
# ---------------------------------------------------------------------------
printf "${CLR_BOLD}DWS Installation Verifier${CLR_RST}  ($(date '+%Y-%m-%d %H:%M:%S'))\n"
printf "Repo root: %s\n" "$REPO_ROOT"

# ===========================================================================
# 1. SYSTEM
# ===========================================================================
section "System"

# Ubuntu version
DISTRO=$(lsb_release -is 2>/dev/null || echo "unknown")
RELEASE=$(lsb_release -rs 2>/dev/null || echo "unknown")
if [[ "$DISTRO" == "Ubuntu" && ("$RELEASE" == "22.04" || "$RELEASE" == "24.04") ]]; then
  _result "Ubuntu version" PASS "$DISTRO $RELEASE"
else
  _result "Ubuntu version" FAIL "Detected: $DISTRO $RELEASE (need 22.04 or 24.04)"
fi

ARCH=$(uname -m)
if [ "$ARCH" = "x86_64" ]; then
  _result "Architecture" PASS "$ARCH"
else
  _result "Architecture" FAIL "$ARCH (need x86_64)"
fi

# ===========================================================================
# 2. PYTHON
# ===========================================================================
section "Python"

if command -v python3 &>/dev/null; then
  PY_SYS_VER=$(python3 --version 2>&1)
  _result "Python (system)" PASS "$PY_SYS_VER"
else
  _result "Python (system)" FAIL "python3 not found in PATH"
fi

if [ -f "$VENV_PYTHON" ]; then
  PY_VENV_VER=$("$VENV_PYTHON" --version 2>&1)
  _result "Python (venv)" PASS "$PY_VENV_VER at $VENV_DIR"
else
  _result "Python (venv)" FAIL "venv not found at $VENV_DIR"
fi

# ===========================================================================
# 3. ROS2
# ===========================================================================
section "ROS2"

ROS_SETUP_FILE="/opt/ros/$ROS_DISTRO/setup.bash"
if [ -f "$ROS_SETUP_FILE" ]; then
  _result "ROS2 installation" PASS "ROS2 $ROS_DISTRO at /opt/ros/$ROS_DISTRO"
else
  _result "ROS2 installation" FAIL "$ROS_SETUP_FILE not found"
fi

if [ -f "$WS_SETUP" ]; then
  _result "Workspace build (install/setup.bash)" PASS "$WS_SETUP"
else
  _result "Workspace build (install/setup.bash)" FAIL "Not found — run: colcon build --packages-select dws_ros2"
fi

if command -v colcon &>/dev/null; then
  _result "colcon" PASS "$(colcon version-check 2>/dev/null | head -1 || echo 'present')"
else
  _result "colcon" WARNING "colcon not in PATH (needed for rebuilds)"
fi

# ===========================================================================
# 4. VIRTUAL ENVIRONMENT — PYTHON IMPORTS
# ===========================================================================
section "Python packages (venv imports)"

REQUIRED_IMPORTS=(
  "numpy:numpy"
  "OpenCV:cv2"
  "Open3D:open3d"
  "pyrealsense2:pyrealsense2"
  "FastAPI:fastapi"
  "uvicorn:uvicorn"
  "aiosqlite:aiosqlite"
  "PyYAML:yaml"
  "pyserial:serial"
  "reportlab:reportlab"
  "pydantic:pydantic"
  "torch:torch"
  "PIL (Pillow):PIL"
  "scipy:scipy"
  "websockets:websockets"
)

if [ -f "$VENV_PYTHON" ]; then
  for entry in "${REQUIRED_IMPORTS[@]}"; do
    label="${entry%%:*}"
    module="${entry##*:}"
    if "$VENV_PYTHON" -c "import $module" 2>/dev/null; then
      ver=$("$VENV_PYTHON" -c "import $module; print(getattr($module,'__version__','?'))" 2>/dev/null || echo "?")
      _result "import $label" PASS "v$ver"
    else
      _result "import $label" FAIL "Cannot import '$module' in $VENV_DIR"
    fi
  done
else
  _result "Python package checks" FAIL "venv not found — skipping all import checks"
fi

# ===========================================================================
# 5. HARDWARE
# ===========================================================================
section "Hardware"

if [ "$OPT_NO_HARDWARE" -eq 1 ]; then
  _result "RealSense camera" WARNING "Skipped (--no-hardware)"
  _result "Scale (/dev/ttyUSB0)" WARNING "Skipped (--no-hardware)"
  _result "Barcode scanner (evdev)" WARNING "Skipped (--no-hardware)"
else
  # RealSense
  if [ -f "$VENV_PYTHON" ]; then
    RS_OUT=$("$VENV_PYTHON" - 2>&1 <<'PYEOF'
import sys
try:
    import pyrealsense2 as rs
    ctx = rs.context()
    devices = list(ctx.query_devices())
    if devices:
        d = devices[0]
        name = d.get_info(rs.camera_info.name)
        sn   = d.get_info(rs.camera_info.serial_number)
        print(f"PASS:{name} S/N {sn}")
    else:
        print("FAIL:No RealSense device found — check USB 3.0 connection")
except Exception as e:
    print(f"FAIL:{e}")
PYEOF
)
    RS_STATUS="${RS_OUT%%:*}"
    RS_DETAIL="${RS_OUT#*:}"
    _result "RealSense camera" "$RS_STATUS" "$RS_DETAIL"
  else
    _result "RealSense camera" FAIL "venv unavailable — cannot probe"
  fi

  # Scale
  if [ -e /dev/ttyUSB0 ]; then
    _result "Scale (/dev/ttyUSB0)" PASS "Device node present"
  elif ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null | head -1 | grep -q tty; then
    ALT_PORT=$(ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null | head -1)
    _result "Scale (/dev/ttyUSB0)" WARNING "Not on ttyUSB0; found $ALT_PORT — update dws_params.yaml"
  else
    _result "Scale (/dev/ttyUSB0)" WARNING "No serial device found — ScaleNode will retry at runtime"
  fi

  # Barcode scanner (USB HID keyboard emulation via evdev)
  if [ -f "$VENV_PYTHON" ]; then
    BC_OUT=$("$VENV_PYTHON" - 2>&1 <<'PYEOF'
import sys
try:
    import evdev
    devs = [evdev.InputDevice(p) for p in evdev.list_devices()]
    barcodes = [d for d in devs if 'barcode' in d.name.lower()
                or 'scanner' in d.name.lower()
                or 'hid' in d.name.lower()]
    if barcodes:
        print(f"PASS:{barcodes[0].name}")
    elif devs:
        print(f"WARNING:No barcode scanner found by name; {len(devs)} HID device(s) present")
    else:
        print("WARNING:No evdev input devices found")
except ImportError:
    print("FAIL:evdev not importable")
except Exception as e:
    print(f"WARNING:{e}")
PYEOF
)
    BC_STATUS="${BC_OUT%%:*}"
    BC_DETAIL="${BC_OUT#*:}"
    _result "Barcode scanner (evdev)" "$BC_STATUS" "$BC_DETAIL"
  else
    _result "Barcode scanner (evdev)" FAIL "venv unavailable — cannot probe"
  fi
fi

# ===========================================================================
# 6. RUNTIME DIRECTORIES & DATABASE
# ===========================================================================
section "Runtime directories & database"

RUNTIME_DIRS=(
  "database"
  "captures"
  "pointclouds"
  "calibration"
  "logs"
  "backups"
  "exports"
)

for d in "${RUNTIME_DIRS[@]}"; do
  full_path="$RUNTIME_BASE/$d"
  if [ -d "$full_path" ]; then
    _result "dws_runtime/$d" PASS "$full_path"
  else
    _result "dws_runtime/$d" FAIL "Missing — run install.sh or: mkdir -p $full_path"
  fi
done

# Pointcloud directory (also checked separately per spec)
if [ -d "$RUNTIME_BASE/pointclouds" ]; then
  PLY_COUNT=$(find "$RUNTIME_BASE/pointclouds" -name "*.ply" 2>/dev/null | wc -l)
  _result "Pointcloud directory" PASS "$PLY_COUNT .ply file(s) present"
else
  _result "Pointcloud directory" FAIL "$RUNTIME_BASE/pointclouds not found"
fi

# SQLite database
DB_PATH="$RUNTIME_BASE/database/dws_data.db"
if [ -f "$DB_PATH" ]; then
  DB_SIZE=$(du -h "$DB_PATH" | cut -f1)
  # Quick integrity check
  if command -v sqlite3 &>/dev/null; then
    INTEGRITY=$(sqlite3 "$DB_PATH" "PRAGMA integrity_check;" 2>&1 | head -1)
    if [ "$INTEGRITY" = "ok" ]; then
      TABLE_COUNT=$(sqlite3 "$DB_PATH" "SELECT count(*) FROM sqlite_master WHERE type='table';" 2>/dev/null || echo "?")
      _result "SQLite database" PASS "$DB_SIZE, $TABLE_COUNT tables, integrity: ok"
    else
      _result "SQLite database" FAIL "PRAGMA integrity_check: $INTEGRITY"
    fi
  else
    _result "SQLite database" PASS "$DB_SIZE (sqlite3 CLI not available for integrity check)"
  fi
else
  _result "SQLite database" WARNING "Not found at $DB_PATH (created on first run)"
fi

# Calibration files
for cal in stable_empty_depth.npy noise_map.npy system_geometry.npy; do
  if [ -f "$RUNTIME_BASE/calibration/$cal" ]; then
    CAL_SIZE=$(du -h "$RUNTIME_BASE/calibration/$cal" | cut -f1)
    _result "Calibration: $cal" PASS "$CAL_SIZE"
  else
    _result "Calibration: $cal" WARNING "Not found — run calibration wizard"
  fi
done

# ===========================================================================
# 7. SERVICES (network endpoints)
# ===========================================================================
section "Running services & network endpoints"

_check_port() {
  local label="$1"
  local port="$2"
  local path="${3:-/}"
  local url="http://localhost:$port$path"

  if command -v curl &>/dev/null; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 "$url" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 500 ]; then
      _result "$label" PASS "HTTP $HTTP_CODE at $url"
    elif [ "$HTTP_CODE" = "000" ]; then
      _result "$label" WARNING "No response at $url — is the service running?"
    else
      _result "$label" WARNING "HTTP $HTTP_CODE at $url"
    fi
  elif command -v nc &>/dev/null; then
    if nc -z -w2 localhost "$port" 2>/dev/null; then
      _result "$label" PASS "Port $port open (curl unavailable for HTTP check)"
    else
      _result "$label" WARNING "Port $port closed — is the service running?"
    fi
  else
    _result "$label" WARNING "Neither curl nor nc available — cannot check port $port"
  fi
}

_check_port "REST API (port $API_PORT)"       "$API_PORT"       "/health"
_check_port "REST API /docs"                  "$API_PORT"       "/docs"
_check_port "REST API /export/pdf"            "$API_PORT"       "/export/pdf"
_check_port "HMI static server ($HMI_STATIC_PORT)" "$HMI_STATIC_PORT" "/"

# WebSocket
if command -v curl &>/dev/null; then
  WS_STATUS=$(curl -s --include \
    --no-buffer \
    --header "Connection: Upgrade" \
    --header "Upgrade: websocket" \
    --header "Host: localhost" \
    --header "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
    --header "Sec-WebSocket-Version: 13" \
    --connect-timeout 3 \
    "http://localhost:$WS_PORT/" 2>/dev/null | head -1 || echo "")
  if echo "$WS_STATUS" | grep -q "101\|Switching"; then
    _result "WebSocket (port $WS_PORT)" PASS "Upgrade 101 accepted"
  elif echo "$WS_STATUS" | grep -q "HTTP"; then
    _result "WebSocket (port $WS_PORT)" WARNING "Port open but non-WebSocket response — service may be starting"
  else
    _result "WebSocket (port $WS_PORT)" WARNING "No response on port $WS_PORT — is run_dws running?"
  fi
else
  _result "WebSocket (port $WS_PORT)" WARNING "curl unavailable — cannot check WebSocket"
fi

# systemd service
SVC_NAME="dws@$USER.service"
if systemctl is-active --quiet "$SVC_NAME" 2>/dev/null; then
  _result "systemd: $SVC_NAME" PASS "active (running)"
elif systemctl is-enabled --quiet "$SVC_NAME" 2>/dev/null; then
  _result "systemd: $SVC_NAME" WARNING "enabled but not active — start with: sudo systemctl start $SVC_NAME"
else
  _result "systemd: $SVC_NAME" WARNING "Unit not found or not enabled — run deploy/install_service.sh"
fi

# ===========================================================================
# SUMMARY
# ===========================================================================
TOTAL=$((PASS_COUNT + FAIL_COUNT + WARN_COUNT))
printf "\n${CLR_BOLD}══════════════════════════════════════════════════\n"
printf "VERIFICATION SUMMARY  —  %d checks\n" "$TOTAL"
printf "══════════════════════════════════════════════════${CLR_RST}\n"
printf "${CLR_PASS}  PASS   : %d${CLR_RST}\n"    "$PASS_COUNT"
printf "${CLR_WARN}  WARNING: %d${CLR_RST}\n"    "$WARN_COUNT"
printf "${CLR_FAIL}  FAIL   : %d${CLR_RST}\n\n"  "$FAIL_COUNT"

if [ "$FAIL_COUNT" -gt 0 ]; then
  printf "${CLR_FAIL}${CLR_BOLD}RESULT: FAIL — %d subsystem(s) require attention.${CLR_RST}\n" "$FAIL_COUNT"
  printf "See INSTALLATION_GUIDE.md → Recovery steps for remediation.\n\n"
elif [ "$WARN_COUNT" -gt 0 ]; then
  printf "${CLR_WARN}${CLR_BOLD}RESULT: PASS WITH WARNINGS — review warnings above.${CLR_RST}\n\n"
else
  printf "${CLR_PASS}${CLR_BOLD}RESULT: ALL PASS — installation verified.${CLR_RST}\n\n"
fi

# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------
if [ "$OPT_JSON" -eq 1 ]; then
  printf '{\n'
  printf '  "timestamp": "%s",\n' "$(date --iso-8601=seconds)"
  printf '  "pass": %d,\n'  "$PASS_COUNT"
  printf '  "warn": %d,\n'  "$WARN_COUNT"
  printf '  "fail": %d,\n'  "$FAIL_COUNT"
  printf '  "results": [\n'
  for i in "${!JSON_RESULTS[@]}"; do
    if [ $i -lt $((${#JSON_RESULTS[@]} - 1)) ]; then
      printf '    %s,\n' "${JSON_RESULTS[$i]}"
    else
      printf '    %s\n'  "${JSON_RESULTS[$i]}"
    fi
  done
  printf '  ]\n'
  printf '}\n'
fi

# Exit code reflects FAIL count
[ "$FAIL_COUNT" -eq 0 ]
