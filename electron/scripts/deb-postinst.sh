#!/bin/bash
set -e

APP_ROOT="/opt/DWS/resources/dws_ws"
VENV="$APP_ROOT/.venv"

# Create runtime directories (best effort)
if [ -n "${SUDO_USER:-}" ]; then
    HOME_DIR="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
    install -d -m 0755 -o "$SUDO_USER" -g "$SUDO_USER"         "$HOME_DIR/.dws/run"         "$HOME_DIR/.dws/logs" || true
fi

# Build Python environment (only once)
if [ ! -x "$VENV/bin/python3" ]; then
    python3 -m venv "$VENV"

    "$VENV/bin/pip" install --no-cache-dir --upgrade pip wheel setuptools

    "$VENV/bin/pip" install --no-cache-dir         -r "$APP_ROOT/requirements.txt"

    "$VENV/bin/pip" install --no-cache-dir         "$APP_ROOT/src/dws_ros2"
fi

command -v update-desktop-database >/dev/null &&
    update-desktop-database -q /usr/share/applications || true

command -v gtk-update-icon-cache >/dev/null &&
    gtk-update-icon-cache -q /usr/share/icons/hicolor || true

exit 0
