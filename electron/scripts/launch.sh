#!/usr/bin/env bash
# DWS Desktop — development launcher
# Usage:
#   ./scripts/launch.sh              # normal start
#   ./scripts/launch.sh --dev        # dev mode (DevTools open)
#   ./scripts/launch.sh --no-hardware # skip hardware checks
#   ./scripts/launch.sh --dev --no-hardware

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(dirname "$SCRIPT_DIR")"

cd "$DESKTOP_DIR"

if [ ! -d "node_modules" ]; then
  echo "[DWS] node_modules not found — running npm install..."
  npm install
fi

if [ ! -f "assets/icons/tray-idle.png" ]; then
  echo "[DWS] Placeholder icons missing — generating..."
  node scripts/make_placeholder_icons.js
fi

echo "[DWS] Launching Electron..."
exec npx electron . "$@"
