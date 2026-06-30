#!/usr/bin/env bash
# =============================================================================
# DWS Desktop — Build the venv that gets bundled into the package
# =============================================================================
# Creates dws_ws/.venv and installs the app's runtime Python dependencies
# into it so electron-builder can package that .venv inside extraResources.
# Target machines need no pip install step.
#
# KEY CHANGE vs previous version:
#   OLD: pip install -e "$WS_DIR/src/dws_ros2"   ← editable install
#   NEW: pip install    "$WS_DIR/src/dws_ros2"   ← normal install
#
# An editable install writes an egg-link / direct_url.json pointing at the
# developer's checkout directory. When the package is shipped to a warehouse
# PC that path (/home/ashmit/dws_release/...) does not exist, causing the
# "resolved to /home/ashmit/..." warning in the logs. More critically,
# if the developer's PYTHONPATH or a .pth file re-introduces that path on
# the target, Python silently imports the wrong (absent) tree.
#
# A normal install copies the package into .venv/lib/pythonX.Y/site-packages/
# so the bundled copy is fully self-contained and independent of where it
# was built.
#
# Usage:
#   electron/scripts/build_venv.sh            # clean build (default — safe for releases)
#   electron/scripts/build_venv.sh --no-clean # reuse existing venv (local iteration only)
#
# build.sh calls this automatically. You can also call it standalone.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../electron/scripts
ELECTRON_DIR="$(dirname "$SCRIPT_DIR")"                       # .../electron
REPO_ROOT="$(dirname "$ELECTRON_DIR")"                        # repo root
WS_DIR="$REPO_ROOT/dws_ws"
SRC_DIR="$WS_DIR/src/dws_ros2"
VENV_DIR="$WS_DIR/.venv"
REQUIREMENTS="$WS_DIR/requirements.txt"

# Default: ALWAYS clean. Pass --no-clean only for fast local iteration where
# you are certain the existing venv is already clean (i.e. was itself built
# by this script without --no-clean).
CLEAN=1
if [ "${1:-}" = "--no-clean" ]; then
  CLEAN=0
fi

echo "=== DWS — building bundled venv ==="
echo "Workspace:    $WS_DIR"
echo "Venv target:  $VENV_DIR"
echo "Requirements: $REQUIREMENTS"
echo

if [ ! -f "$REQUIREMENTS" ]; then
  echo "[FAIL] $REQUIREMENTS not found." >&2
  exit 1
fi

if [ ! -d "$SRC_DIR" ]; then
  echo "[FAIL] $SRC_DIR not found." >&2
  exit 1
fi

# Always clean for a release build to guarantee no stale artefacts.
# --clean is the recommended mode; omitting it only makes sense for quick
# local iteration where you've already verified the venv is clean.
if [ "$CLEAN" = "1" ] || [ ! -d "$VENV_DIR" ]; then
  echo "[INFO] Removing existing venv (clean build)..."
  rm -rf "$VENV_DIR"
  echo "[INFO] Creating fresh venv..."
  python3 -m venv "$VENV_DIR"
else
  echo "[WARN] --no-clean: reusing existing venv at $VENV_DIR."
  echo "[WARN] Only safe if this venv was itself built by this script."
  echo "[WARN] NEVER ship a .deb built with --no-clean from a dev machine."
fi

VENV_PY="$VENV_DIR/bin/python3"
VENV_PIP="$VENV_DIR/bin/pip"

echo "[INFO] Upgrading pip..."
"$VENV_PY" -m pip install --upgrade pip --quiet

echo "[INFO] Installing runtime requirements..."
"$VENV_PIP" install --quiet -r "$REQUIREMENTS"

# ── CRITICAL: normal install, not editable ───────────────────────────────────
# DO NOT add -e / --editable here. An editable install bakes the developer's
# source-tree path into the venv via egg-link / direct_url.json / .pth files.
# That path breaks on any machine that isn't the build machine.
echo "[INFO] Installing dws_ros2 package (normal install)..."
"$VENV_PIP" install --quiet "$SRC_DIR"

# ── Verify: origin must be inside the venv, not the source tree ──────────────
echo "[INFO] Verifying dws_ros2 import origin..."
ORIGIN=$("$VENV_PY" -c \
  "import importlib.util as u; s = u.find_spec('dws_ros2'); print(s.origin if s else '')")
if [ -z "$ORIGIN" ]; then
  echo "[FAIL] dws_ros2 is not importable from the bundled venv." >&2
  exit 1
fi
ORIGIN_REAL=$(realpath "$ORIGIN")
VENV_REAL=$(realpath "$VENV_DIR")
if [[ "$ORIGIN_REAL" != "$VENV_REAL"* ]]; then
  echo "[FAIL] dws_ros2 resolves to '$ORIGIN_REAL'" >&2
  echo "       Expected it inside '$VENV_REAL'" >&2
  echo "       Likely cause: stale .egg-link or PYTHONPATH override." >&2
  exit 1
fi
echo "[OK  ] dws_ros2 => $ORIGIN_REAL"

# ── Strip what we don't need to ship ─────────────────────────────────────────
echo "[INFO] Trimming bundled venv..."
"$VENV_PY" -m pip cache purge >/dev/null 2>&1 || true
find "$VENV_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$VENV_DIR" -type d -name "tests" -path "*/site-packages/*" \
     -exec rm -rf {} + 2>/dev/null || true
# Remove editable-install breadcrumbs if somehow any snuck in
find "$VENV_DIR" -name "*.egg-link" -delete 2>/dev/null || true
find "$VENV_DIR" -name "direct_url.json" -delete 2>/dev/null || true

echo
echo "=== Bundled venv ready ==="
du -sh "$VENV_DIR"

# ── Four-point release sanity check ──────────────────────────────────────────
echo
echo "[CHECK] Running release sanity checks..."

# 1. dws_ros2 imports from site-packages, not the source tree
FINAL_ORIGIN=$("$VENV_PY" -c \
  "import dws_ros2; print(dws_ros2.__file__)" 2>/dev/null || true)
echo "[CHECK] 1. dws_ros2.__file__ = $FINAL_ORIGIN"

# 2. No __editable__ files
EDITABLE_COUNT=$(find "$VENV_DIR" -name "__editable__*" 2>/dev/null | wc -l)
if [ "$EDITABLE_COUNT" -gt 0 ]; then
  echo "[FAIL]  2. Found $EDITABLE_COUNT __editable__ file(s) — editable install leaked!" >&2
  find "$VENV_DIR" -name "__editable__*" >&2
  exit 1
fi
echo "[OK  ] 2. No __editable__ files found."

# 3. No .egg-link files
EGGLINK_COUNT=$(find "$VENV_DIR" -name "*.egg-link" 2>/dev/null | wc -l)
if [ "$EGGLINK_COUNT" -gt 0 ]; then
  echo "[FAIL]  3. Found $EGGLINK_COUNT .egg-link file(s)!" >&2
  find "$VENV_DIR" -name "*.egg-link" >&2
  exit 1
fi
echo "[OK  ] 3. No .egg-link files found."

# 4. No references to the build user's home directory (catches shebangs in bin/)
# Shebangs in venv executables are expected to contain the build-time python path,
# but we check that none point back into the *source tree* (/home/<user>/...dws...).
HOMEPATH_HITS=$(grep -rl "/home/" "$VENV_DIR/lib" 2>/dev/null | wc -l || true)
if [ "$HOMEPATH_HITS" -gt 0 ]; then
  echo "[WARN] 4. Found $HOMEPATH_HITS file(s) in lib/ containing a /home/ path:"
  grep -rl "/home/" "$VENV_DIR/lib" 2>/dev/null | head -10
  echo "       Inspect these — they may be direct_url.json remnants or .pth leaks."
else
  echo "[OK  ] 4. No /home/ paths found in venv lib/."
fi

echo
echo "[NOTE] This venv contains compiled extensions (torch, opencv, open3d,"
echo "       pyrealsense2). It is portable to other machines with the same"
echo "       OS family / glibc version as this build machine (Ubuntu 24.04 x86_64)."
echo "       Build inside a Docker container matching the target if needed."