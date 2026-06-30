#!/bin/bash
# =============================================================================
# Registers dws.service with systemd for THIS checkout and THIS user.
#
# This is just the systemd-registration step of Milestone 1 (reliability).
# It is intentionally narrow — it does not install apt/pip dependencies,
# build the workspace, or verify hardware. That is the scope of the full
# install.sh (Milestone 5), not yet implemented.
#
# Usage:
#   sudo deploy/install_service.sh
# =============================================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi

# The real (non-root) user who invoked sudo — the service runs as them,
# not as root, since ROS2/colcon/RealSense userspace tooling assumes a
# normal user environment.
TARGET_USER="${SUDO_USER:-$USER}"
if [ "$TARGET_USER" = "root" ]; then
  echo "Refusing to install the service to run as root." >&2
  echo "Re-run with: sudo -u <your-user> -E true  (to confirm SUDO_USER is set), then sudo $0" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../dws_ros2/deploy
PKG_DIR="$(dirname "$SCRIPT_DIR")"                            # .../dws_ros2

UNIT_SRC="$SCRIPT_DIR/dws.service"
UNIT_DST="/etc/systemd/system/dws@.service"

echo "[install] Templating $UNIT_SRC -> $UNIT_DST"
sed "s#__DWS_PKG_DIR__#$PKG_DIR#g" "$UNIT_SRC" > "$UNIT_DST"

chmod +x "$SCRIPT_DIR/run.sh" "$SCRIPT_DIR/stop.sh"

echo "[install] systemctl daemon-reload"
systemctl daemon-reload

echo "[install] Enabling dws@${TARGET_USER}.service"
systemctl enable "dws@${TARGET_USER}.service"

echo ""
echo "Installed. Next steps:"
echo "  Start now:        sudo systemctl start dws@${TARGET_USER}.service"
echo "  Check status:     systemctl status dws@${TARGET_USER}.service"
echo "  Follow logs:      journalctl -u dws@${TARGET_USER}.service -f"
echo "  Auto-starts on boot and auto-restarts on crash (RestartSec=5)."
