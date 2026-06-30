#!/bin/bash
# =============================================================================
# DWS System — Diagnostic Report Generator
# =============================================================================
# Collects complete system, software, hardware, and DWS state information
# and writes it to diagnostic_report.txt.
#
# Usage:
#   bash deploy/diagnostics.sh [--out <path>] [--no-hardware]
#
#   --out <path>    Override output file (default: ./diagnostic_report.txt)
#   --no-hardware   Skip live hardware probes
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

API_PORT="${DWS_API_PORT:-8000}"
HMI_STATIC_PORT="${DWS_HMI_STATIC_PORT:-8080}"
WS_PORT="${DWS_WS_PORT:-8765}"

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
REPORT_PATH="${DWS_DIAG_OUT:-$REPO_ROOT/diagnostic_report.txt}"
OPT_NO_HARDWARE=0
_i=1
while [ $_i -le $# ]; do
  arg="${!_i}"
  case "$arg" in
    --out)
      _i=$((_i + 1))
      REPORT_PATH="${!_i}"
      ;;
    --no-hardware) OPT_NO_HARDWARE=1 ;;
    -h|--help)
      grep '^#' "$0" | head -12 | sed 's/^# \{0,1\}//'
      exit 0
      ;;
  esac
  _i=$((_i + 1))
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
REPORT_STARTED="$(date '+%Y-%m-%d %H:%M:%S')"
# Start fresh
: > "$REPORT_PATH"

r()  { printf "%s\n" "$*" | tee -a "$REPORT_PATH"; }
rh() { r ""; r "══════════════════════════════════════════════════════════"; r "  $*"; r "══════════════════════════════════════════════════════════"; }
rs() { r ""; r "── $*"; }
rk() {
  # rk KEY VALUE — right-aligned key column
  printf "  %-30s %s\n" "$1" "$2" | tee -a "$REPORT_PATH"
}
rcmd() {
  # rcmd LABEL COMMAND — run command, indent output
  r "  $1:"
  local output
  output=$(eval "$2" 2>&1) || true
  while IFS= read -r line; do
    r "    $line"
  done <<< "$output"
}

# ---------------------------------------------------------------------------
# HEADER
# ---------------------------------------------------------------------------
r "┌─────────────────────────────────────────────────────────────────┐"
r "│             DWS SYSTEM — DIAGNOSTIC REPORT                     │"
r "├─────────────────────────────────────────────────────────────────┤"
r "│  Generated : $REPORT_STARTED                           │"
r "│  User      : ${USER:-$(id -un)}                                              │"
r "│  Host      : $(hostname)                                        │"
r "└─────────────────────────────────────────────────────────────────┘"

# ===========================================================================
# 1. SYSTEM
# ===========================================================================
rh "1. SYSTEM"

rs "Operating System"
rk "Distributor"  "$(lsb_release -is 2>/dev/null || echo 'unknown')"
rk "Release"      "$(lsb_release -rs 2>/dev/null || echo 'unknown')"
rk "Codename"     "$(lsb_release -cs 2>/dev/null || echo 'unknown')"
rk "Description"  "$(lsb_release -ds 2>/dev/null || echo 'unknown')"

rs "Kernel"
rk "Kernel"       "$(uname -r)"
rk "Architecture" "$(uname -m)"
rk "Hostname"     "$(hostname -f 2>/dev/null || hostname)"
rk "Uptime"       "$(uptime -p 2>/dev/null || uptime)"

rs "CPU"
if command -v lscpu &>/dev/null; then
  rk "Model"     "$(lscpu | grep 'Model name' | sed 's/.*:\s*//')"
  rk "Cores"     "$(lscpu | grep '^CPU(s):' | awk '{print $2}')"
  rk "Threads"   "$(lscpu | grep '^Thread(s) per core:' | awk '{print $4}')"
  rk "MHz"       "$(lscpu | grep 'CPU MHz' | head -1 | sed 's/.*:\s*//')"
else
  rk "CPU info"  "$(grep 'model name' /proc/cpuinfo | head -1 | sed 's/.*:\s*//')"
fi

rs "RAM"
if command -v free &>/dev/null; then
  TOTAL_RAM=$(free -h | awk '/Mem:/{print $2}')
  USED_RAM=$(free -h  | awk '/Mem:/{print $3}')
  FREE_RAM=$(free -h  | awk '/Mem:/{print $4}')
  rk "Total"    "$TOTAL_RAM"
  rk "Used"     "$USED_RAM"
  rk "Free"     "$FREE_RAM"
fi

rs "Disk"
df -h 2>/dev/null | while IFS= read -r line; do r "  $line"; done

rs "Load average"
rk "Load" "$(cat /proc/loadavg)"

rs "Network interfaces"
if command -v ip &>/dev/null; then
  ip -brief addr 2>/dev/null | while IFS= read -r line; do r "  $line"; done
fi

# ===========================================================================
# 2. SOFTWARE VERSIONS
# ===========================================================================
rh "2. SOFTWARE"

rs "Python"
rk "System python3" "$(python3 --version 2>&1 || echo 'not found')"
if [ -f "$VENV_PYTHON" ]; then
  rk "Venv python3" "$("$VENV_PYTHON" --version 2>&1)"
  rk "Venv pip"     "$("$VENV_DIR/bin/pip" --version 2>&1 | head -1)"
else
  rk "Venv python3" "NOT FOUND at $VENV_DIR"
fi

rs "ROS2"
_UBUNTU_RELEASE=$(lsb_release -rs 2>/dev/null || echo "unknown")
case "$_UBUNTU_RELEASE" in
  24.04) ROS_DISTRO="jazzy"  ;;
  22.04) ROS_DISTRO="humble" ;;
  *)     ROS_DISTRO="jazzy"  ;;
esac
if [ -d "/opt/ros/$ROS_DISTRO" ]; then
  rk "ROS2 distro"  "$ROS_DISTRO"
  rk "ROS2 root"    "/opt/ros/$ROS_DISTRO"
  rk "colcon"       "$(colcon version-check 2>/dev/null | head -1 || echo 'present')"
else
  rk "ROS2"         "NOT FOUND at /opt/ros/$ROS_DISTRO"
fi

rs "Key Python libraries (venv)"
if [ -f "$VENV_PYTHON" ]; then
  LIBS=(
    "numpy:numpy"
    "OpenCV:cv2"
    "Open3D:open3d"
    "pyrealsense2:pyrealsense2"
    "torch:torch"
    "fastapi:fastapi"
    "uvicorn:uvicorn"
    "pydantic:pydantic"
    "aiosqlite:aiosqlite"
    "PIL:PIL"
    "scipy:scipy"
    "reportlab:reportlab"
    "serial:serial"
  )
  for entry in "${LIBS[@]}"; do
    label="${entry%%:*}"
    module="${entry##*:}"
    ver=$("$VENV_PYTHON" -c "import $module; print(getattr($module,'__version__','?'))" 2>/dev/null || echo "NOT INSTALLED")
    rk "$label" "$ver"
  done
else
  r "  Venv not available — skipping library checks"
fi

rs "System packages"
for pkg in librealsense2-dkms sqlite3 git curl; do
  if dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
    VER=$(dpkg -l "$pkg" 2>/dev/null | awk '/^ii/{print $3}' | head -1)
    rk "$pkg" "$VER"
  else
    rk "$pkg" "NOT INSTALLED"
  fi
done

rs "librealsense2 SDK"
if command -v rs-enumerate-devices &>/dev/null; then
  rk "rs-enumerate-devices" "$(rs-enumerate-devices --version 2>/dev/null || echo 'present')"
else
  rk "rs-enumerate-devices" "not found in PATH"
fi

# ===========================================================================
# 3. HARDWARE
# ===========================================================================
rh "3. HARDWARE"

rs "Intel RealSense Camera"
if [ "$OPT_NO_HARDWARE" -eq 1 ]; then
  rk "Status" "Skipped (--no-hardware)"
elif [ -f "$VENV_PYTHON" ]; then
  "$VENV_PYTHON" - 2>&1 | while IFS= read -r line; do
    r "  $line"
  done << 'PYEOF'
try:
    import pyrealsense2 as rs
    ctx = rs.context()
    devices = list(ctx.query_devices())
    if not devices:
        print("No RealSense devices found")
    for d in devices:
        print(f"  Name:          {d.get_info(rs.camera_info.name)}")
        print(f"  Serial Number: {d.get_info(rs.camera_info.serial_number)}")
        print(f"  Firmware:      {d.get_info(rs.camera_info.firmware_version)}")
        print(f"  USB:           {d.get_info(rs.camera_info.usb_type_descriptor)}")
except Exception as e:
    print(f"  Error: {e}")
PYEOF
else
  rk "Status" "Venv unavailable — cannot probe"
fi

rs "USB devices"
if command -v lsusb &>/dev/null; then
  lsusb 2>/dev/null | while IFS= read -r line; do r "  $line"; done
fi

rs "Serial / Scale"
if ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null | grep -q tty; then
  ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null | while IFS= read -r line; do r "  $line"; done
else
  r "  No /dev/ttyUSB* or /dev/ttyACM* devices found"
fi

rs "Barcode scanner (evdev input devices)"
if [ "$OPT_NO_HARDWARE" -eq 1 ]; then
  rk "Status" "Skipped (--no-hardware)"
elif [ -f "$VENV_PYTHON" ]; then
  "$VENV_PYTHON" - 2>&1 | while IFS= read -r line; do r "  $line"; done << 'PYEOF'
try:
    import evdev
    devs = evdev.list_devices()
    if devs:
        for path in devs:
            try:
                d = evdev.InputDevice(path)
                print(f"  {path}: {d.name}")
            except Exception:
                print(f"  {path}: (permission denied)")
    else:
        print("  No evdev input devices found")
except ImportError:
    print("  evdev not importable — install with: pip install evdev")
except Exception as e:
    print(f"  Error: {e}")
PYEOF
else
  r "  Venv unavailable"
fi

# ===========================================================================
# 4. DWS STATE
# ===========================================================================
rh "4. DWS SYSTEM STATE"

rs "Repository"
rk "Repo root"  "$REPO_ROOT"
if [ -d "$REPO_ROOT/.git" ]; then
  rk "Git branch" "$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')"
  rk "Git commit" "$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
  rk "Git status" "$(git -C "$REPO_ROOT" status --short 2>/dev/null | wc -l) modified file(s)"
fi
if [ -f "$REPO_ROOT/VERSION" ]; then
  cat "$REPO_ROOT/VERSION" | while IFS= read -r line; do rk "VERSION" "$line"; break; done
fi

rs "Configuration"
if [ -f "$CONFIG_FILE" ]; then
  rk "Config file" "$CONFIG_FILE"
  grep -v "^#\|^$" "$CONFIG_FILE" | while IFS= read -r line; do r "    $line"; done
else
  rk "Config file" "NOT FOUND at $CONFIG_FILE"
fi

rs "Runtime directories"
RUNTIME_DIRS=(database captures pointclouds calibration logs backups exports)
for d in "${RUNTIME_DIRS[@]}"; do
  FULL="$RUNTIME_BASE/$d"
  if [ -d "$FULL" ]; then
    SIZE=$(du -sh "$FULL" 2>/dev/null | cut -f1)
    COUNT=$(find "$FULL" -maxdepth 3 -type f 2>/dev/null | wc -l)
    rk "dws_runtime/$d" "$SIZE  ($COUNT file(s))"
  else
    rk "dws_runtime/$d" "MISSING"
  fi
done

rs "SQLite database"
DB_PATH="$RUNTIME_BASE/database/dws_data.db"
if [ -f "$DB_PATH" ]; then
  rk "Path"   "$DB_PATH"
  rk "Size"   "$(du -h "$DB_PATH" | cut -f1)"
  rk "Mtime"  "$(stat -c '%y' "$DB_PATH" 2>/dev/null | cut -d. -f1)"
  if command -v sqlite3 &>/dev/null; then
    rk "Integrity" "$(sqlite3 "$DB_PATH" 'PRAGMA integrity_check;' 2>/dev/null | head -1)"
    r ""
    r "  Tables:"
    sqlite3 "$DB_PATH" ".tables" 2>/dev/null | tr ' ' '\n' | grep -v '^$' | \
      while IFS= read -r t; do
        COUNT=$(sqlite3 "$DB_PATH" "SELECT count(*) FROM \"$t\";" 2>/dev/null || echo "?")
        rk "    $t" "$COUNT row(s)"
      done
  fi
else
  rk "Status" "Not found — will be created on first run"
fi

rs "Calibration files"
for cal in stable_empty_depth.npy noise_map.npy system_geometry.npy calibration_result.jpg; do
  CAL_PATH="$RUNTIME_BASE/calibration/$cal"
  if [ -f "$CAL_PATH" ]; then
    rk "$cal" "$(du -h "$CAL_PATH" | cut -f1)  mtime: $(stat -c '%y' "$CAL_PATH" 2>/dev/null | cut -d. -f1)"
  else
    rk "$cal" "NOT FOUND"
  fi
done

rs "Active systemd services"
SVC_NAME="dws@$USER.service"
rk "$SVC_NAME" "$(systemctl is-active "$SVC_NAME" 2>/dev/null || echo 'not found')"
systemctl status "$SVC_NAME" 2>/dev/null | head -8 | while IFS= read -r line; do r "  $line"; done

rs "Running ports"
if command -v ss &>/dev/null; then
  r "  (DWS-related ports: $API_PORT $HMI_STATIC_PORT $WS_PORT)"
  ss -tlnp 2>/dev/null | grep -E ":($API_PORT|$HMI_STATIC_PORT|$WS_PORT|8090) " | \
    while IFS= read -r line; do r "  $line"; done || r "  (none of the DWS ports appear to be listening)"
fi

rs "Log file locations"
rk "install.log"   "$REPO_ROOT/install.log"
rk "backend.log"   "$HOME/.dws/logs/backend.log"
rk "api.log"       "$HOME/.dws/logs/api.log"
rk "hmi.log"       "$HOME/.dws/logs/hmi.log"
rk "runtime logs"  "$RUNTIME_BASE/logs/"
r ""
r "  Recent backend log (last 20 lines):"
BACKEND_LOG="$HOME/.dws/logs/backend.log"
if [ -f "$BACKEND_LOG" ]; then
  tail -20 "$BACKEND_LOG" | while IFS= read -r line; do r "    $line"; done
else
  r "    (no backend.log found)"
fi

# ===========================================================================
# FOOTER
# ===========================================================================
rh "END OF DIAGNOSTIC REPORT"
r ""
r "  Generated : $REPORT_STARTED"
r "  Saved to  : $REPORT_PATH"
r ""

printf "\nDiagnostic report written to: %s\n" "$REPORT_PATH"
