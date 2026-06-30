#!/usr/bin/env bash
# =============================================================================
# DWS — Top-level production build script
# =============================================================================
# Single entry point for a fully reproducible build on a fresh Ubuntu 24.04.
#
# Usage:
#   git clone <repo> && cd dws_final_release
#   ./build.sh              # full clean build (recommended for releases)
#   ./build.sh --skip-venv  # reuse existing .venv (faster local iteration —
#                           #   ONLY safe if venv was built by this script on
#                           #   the same machine; NEVER use for shipping .debs)
#
# Produces:
#   electron/dist/dws-desktop_1.0.0_amd64.deb
#   electron/dist/DWS-1.0.0.AppImage
#
# Requirements (auto-checked below):
#   - node >= 18, npm
#   - python3 >= 3.10, python3-venv
#   - dpkg, fakeroot  (for .deb)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ELECTRON_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$ELECTRON_DIR")"

WS_DIR="$REPO_ROOT/dws_ws"
SRC_DIR="$WS_DIR/src/dws_ros2"
VENV_DIR="$WS_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python3"

SKIP_VENV=0
for arg in "$@"; do
  [ "$arg" = "--skip-venv" ] && SKIP_VENV=1
done

log()  { echo "[build] $*"; }
fail() { echo "[FAIL]  $*" >&2; exit 1; }

# ── 0. Preflight ──────────────────────────────────────────────────────────────
log "=== DWS Production Build ==="
log "Repo root: $REPO_ROOT"
echo

command -v python3 >/dev/null || fail "python3 not found"
python3 -m venv --help >/dev/null 2>&1 || fail "python3-venv not installed (apt install python3-venv)"
command -v node   >/dev/null || fail "node not found"
command -v npm    >/dev/null || fail "npm not found"
command -v dpkg   >/dev/null || log "WARN: dpkg not found — .deb will not be buildable"

log "python3: $(python3 --version)"
log "node:    $(node --version)"
log "npm:     $(npm --version)"
echo

# ── 1. Clean previous build artefacts ─────────────────────────────────────────
log "Cleaning previous build artefacts..."
rm -rf "$ELECTRON_DIR/dist"

# Remove any stale editable-install artefacts from the source tree.
# These are the primary cause of "resolves to /home/ashmit/..." path leakage.
find "$WS_DIR" -name "*.egg-link"      -delete 2>/dev/null || true
find "$WS_DIR" -name "*.egg-info" -type d -exec rm -rf {} + 2>/dev/null || true
find "$WS_DIR" -name "direct_url.json" -delete 2>/dev/null || true
find "$WS_DIR" -name "RECORD"          -delete 2>/dev/null || true
find "$SRC_DIR" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$SRC_DIR" -name "*.pyc" -delete 2>/dev/null || true
log "Done."
echo

# ── 2. Fresh virtual environment ──────────────────────────────────────────────
if [ "$SKIP_VENV" = "1" ] && [ -d "$VENV_DIR" ]; then
  log "WARN: --skip-venv: reusing existing venv at $VENV_DIR"
  log "WARN: ONLY safe if this venv was built by this script on this machine."
  log "WARN: NEVER ship a .deb built with --skip-venv from a dev machine."
else
  log "Removing old venv (if any)..."
  rm -rf "$VENV_DIR"

  log "Creating fresh venv..."
  python3 -m venv "$VENV_DIR"

  log "Upgrading pip..."
  "$VENV_PY" -m pip install --upgrade pip --quiet

  log "Installing runtime requirements from requirements.txt..."
  "$VENV_DIR/bin/pip" install --quiet -r "$WS_DIR/requirements.txt"

  # Install dws_ros2 as a NORMAL (non-editable) install.
  # This is the critical change vs the old build_venv.sh which used `pip install -e`.
  # A normal install copies the package into .venv/lib/pythonX.Y/site-packages/
  # so it is completely independent of the source tree path at runtime.
  log "Installing dws_ros2 (normal install, not editable)..."
  "$VENV_DIR/bin/pip" install --quiet "$SRC_DIR"

  log "Venv built: $(du -sh "$VENV_DIR" | cut -f1)"
fi
echo

# ── 3. Verify: dws_ros2 must resolve inside the venv, never the source tree ──
log "Verifying dws_ros2 import origin..."
ORIGIN=$("$VENV_PY" -c "import importlib.util as u; s = u.find_spec('dws_ros2'); print(s.origin or '')")
if [ -z "$ORIGIN" ]; then
  fail "dws_ros2 is not importable from $VENV_PY"
fi
ORIGIN_REAL=$(realpath "$ORIGIN")
VENV_REAL=$(realpath "$VENV_DIR")
if [[ "$ORIGIN_REAL" != "$VENV_REAL"* ]]; then
  fail "dws_ros2 resolves to '$ORIGIN_REAL' which is OUTSIDE the venv '$VENV_REAL'. \
This means an editable install or PYTHONPATH leak is still present. \
Check for .egg-link files or site.py .pth entries."
fi
log "OK — dws_ros2 resolves to: $ORIGIN_REAL"
echo

# ── 4. Npm dependencies ───────────────────────────────────────────────────────
cd "$ELECTRON_DIR"
if [ ! -d "node_modules" ]; then
  log "Installing npm dependencies..."
  npm install
else
  log "node_modules already present (run 'npm install' manually if you need to refresh)"
fi

# ── 5. Placeholder icons (if missing) ────────────────────────────────────────
if [ ! -f "assets/icons/icon.png" ]; then
  log "Generating placeholder icons..."
  node scripts/make_placeholder_icons.js
fi

# ── 6. electron-builder ──────────────────────────────────────────────────────
log "Running electron-builder..."
npm run build
echo

# ── 7. Post-build verification ───────────────────────────────────────────────
log "=== Post-build verification ==="

# Find the bundled venv inside the unpacked resources tree.
# electron-builder --dir writes to dist/linux-unpacked/resources/
UNPACKED="$ELECTRON_DIR/dist/linux-unpacked"
if [ -d "$UNPACKED" ]; then
  BUNDLED_PY="$UNPACKED/resources/dws_ws/.venv/bin/python3"
  if [ -x "$BUNDLED_PY" ]; then
    log "Checking bundled interpreter: $BUNDLED_PY"
    BUNDLED_ORIGIN=$("$BUNDLED_PY" -c \
      "import importlib.util as u; s = u.find_spec('dws_ros2'); print(s.origin or '')" 2>/dev/null || true)
    if [ -z "$BUNDLED_ORIGIN" ]; then
      log "WARN: dws_ros2 not importable from bundled python. \
This may be expected if the bundled venv's sys.prefix doesn't match its installed location — \
the runtime corrects for this via PYTHONPATH. Skipping bundled-import check."
    else
      RESOURCES_DIR="$UNPACKED/resources"
      if [[ "$(realpath "$BUNDLED_ORIGIN")" == "$(realpath "$RESOURCES_DIR")"* ]]; then
        log "OK  — bundled dws_ros2 origin: $BUNDLED_ORIGIN"
      else
        # Check whether it leaks back to the developer source tree
        if [[ "$(realpath "$BUNDLED_ORIGIN")" == "$REPO_ROOT"* ]]; then
          fail "Bundled dws_ros2 still resolves to source tree: $BUNDLED_ORIGIN — editable install leaked!"
        else
          log "WARN: bundled dws_ros2 origin '$BUNDLED_ORIGIN' is outside resources/ — \
may indicate a venv relocation issue; double-check on a target machine."
        fi
      fi
    fi
  else
    log "WARN: bundled python not found at $BUNDLED_PY — cannot run bundled import check"
  fi
else
  log "No unpacked dir found (dir-only build was not requested) — skipping bundled import check"
fi

log ""
log "=== Build complete ==="
ls -lh "$ELECTRON_DIR/dist/"*.deb "$ELECTRON_DIR/dist/"*.AppImage 2>/dev/null \
  || ls -lh "$ELECTRON_DIR/dist/" 2>/dev/null \
  || log "(check $ELECTRON_DIR/dist/)"