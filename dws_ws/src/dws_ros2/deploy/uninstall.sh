#!/bin/bash
# =============================================================================
# DWS System — Uninstall Script
# =============================================================================
# Safely removes the DWS service, shortcuts, and optionally runtime data.
# Prompts before deleting any user data (database, captures, pointclouds).
#
# Usage:
#   bash deploy/uninstall.sh [--keep-data] [--force] [--dry-run]
#
#   --keep-data   Preserve dws_runtime/ entirely (service removed only)
#   --force       Skip all confirmation prompts (non-interactive / CI)
#   --dry-run     Show what would be removed; make no changes
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
RUNTIME_BASE="$REPO_ROOT/dws_runtime"

SVC_NAME="dws@$USER.service"
SVC_UNIT="/etc/systemd/system/dws@.service"

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
OPT_KEEP_DATA=0
OPT_FORCE=0
OPT_DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --keep-data) OPT_KEEP_DATA=1 ;;
    --force)     OPT_FORCE=1 ;;
    --dry-run)   OPT_DRY_RUN=1 ;;
    -h|--help)
      grep '^#' "$0" | head -14 | sed 's/^# \{0,1\}//'
      exit 0
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
UNINSTALL_LOG="$REPO_ROOT/uninstall_$(date '+%Y%m%d_%H%M%S').log"
log()  { printf "[%s] %s\n" "$(date '+%H:%M:%S')" "$*" | tee -a "$UNINSTALL_LOG"; }
ok()   { log "[OK ] $*"; }
warn() { log "[WARN] $*"; }
skip() { log "[SKIP] $*"; }

dry() {
  # dry DESCRIPTION COMMAND...
  local desc="$1"; shift
  if [ "$OPT_DRY_RUN" -eq 1 ]; then
    log "[DRY ] Would: $desc"
  else
    log "       $desc"
    "$@" 2>&1 | tee -a "$UNINSTALL_LOG" || warn "Command failed: $*"
  fi
}

prompt_yes() {
  # prompt_yes QUESTION — returns 0 (yes) or 1 (no)
  if [ "$OPT_FORCE" -eq 1 ]; then return 0; fi
  printf "\n  %s [y/N] " "$1"
  read -r _ans
  case "$_ans" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

# ---------------------------------------------------------------------------
# START
# ---------------------------------------------------------------------------
log "DWS Uninstall starting — $(date '+%Y-%m-%d %H:%M:%S')"
[ "$OPT_DRY_RUN" -eq 1 ] && log "DRY RUN — no changes will be made"

printf "\n"
printf "  ╔═══════════════════════════════════════════╗\n"
printf "  ║        DWS SYSTEM UNINSTALLER             ║\n"
printf "  ╚═══════════════════════════════════════════╝\n\n"

if [ "$OPT_FORCE" -eq 0 ] && [ "$OPT_DRY_RUN" -eq 0 ]; then
  printf "  This will remove the DWS service and build artefacts.\n"
  printf "  You will be prompted individually before any user data\n"
  printf "  (database, captures, pointclouds) is deleted.\n\n"
  prompt_yes "Proceed with uninstall?" || { log "Uninstall cancelled."; exit 0; }
fi

# ===========================================================================
# PHASE 1 — STOP AND DISABLE SERVICE
# ===========================================================================
log ""
log "═══ Phase 1: Stop and disable systemd service ═══"

if systemctl is-active --quiet "$SVC_NAME" 2>/dev/null; then
  dry "Stop $SVC_NAME" sudo systemctl stop "$SVC_NAME"
  ok "Service stopped"
else
  skip "Service not running"
fi

if systemctl is-enabled --quiet "$SVC_NAME" 2>/dev/null; then
  dry "Disable $SVC_NAME" sudo systemctl disable "$SVC_NAME"
  ok "Service disabled"
else
  skip "Service not enabled"
fi

if [ -f "$SVC_UNIT" ]; then
  dry "Remove unit file $SVC_UNIT" sudo rm -f "$SVC_UNIT"
  ok "Unit file removed"
else
  skip "Unit file not present at $SVC_UNIT"
fi

if [ "$OPT_DRY_RUN" -eq 0 ]; then
  sudo systemctl daemon-reload 2>/dev/null || true
  ok "systemctl daemon-reload"
fi

# ===========================================================================
# PHASE 2 — REMOVE DESKTOP SHORTCUTS
# ===========================================================================
log ""
log "═══ Phase 2: Remove desktop shortcuts ═══"

SHORTCUT_LOCATIONS=(
  "$HOME/Desktop/DWS.desktop"
  "$HOME/.local/share/applications/dws.desktop"
  "/usr/share/applications/dws.desktop"
)

for shortcut in "${SHORTCUT_LOCATIONS[@]}"; do
  if [ -f "$shortcut" ]; then
    dry "Remove shortcut: $shortcut" rm -f "$shortcut"
    ok "Removed: $shortcut"
  else
    skip "Not found: $shortcut"
  fi
done

# PATH entry in .bashrc / .profile
for rc_file in "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"; do
  if [ -f "$rc_file" ] && grep -q "dws_ros2\|dws_ws" "$rc_file" 2>/dev/null; then
    log "Found DWS entries in $rc_file:"
    grep -n "dws_ros2\|dws_ws\|DWS" "$rc_file" | while IFS= read -r line; do
      log "  $line"
    done
    if prompt_yes "Remove DWS lines from $rc_file?"; then
      if [ "$OPT_DRY_RUN" -eq 0 ]; then
        cp "$rc_file" "${rc_file}.dws_backup"
        grep -v "dws_ros2\|dws_ws\|DWS" "$rc_file" > "${rc_file}.tmp" && \
          mv "${rc_file}.tmp" "$rc_file"
        ok "Cleaned $rc_file (backup: ${rc_file}.dws_backup)"
      else
        log "[DRY ] Would clean $rc_file"
      fi
    else
      skip "Left $rc_file unchanged"
    fi
  fi
done

# ===========================================================================
# PHASE 3 — REMOVE BUILD ARTEFACTS & VENV
# ===========================================================================
log ""
log "═══ Phase 3: Remove build artefacts and virtual environment ═══"

BUILD_DIRS=(
  "$WS_DIR/build"
  "$WS_DIR/install"
  "$WS_DIR/log"
)

for d in "${BUILD_DIRS[@]}"; do
  if [ -d "$d" ]; then
    dry "Remove $d" rm -rf "$d"
    ok "Removed: $d"
  else
    skip "Not found: $d"
  fi
done

if [ -d "$VENV_DIR" ]; then
  if prompt_yes "Remove Python virtual environment ($VENV_DIR)?"; then
    dry "Remove venv $VENV_DIR" rm -rf "$VENV_DIR"
    ok "Virtual environment removed"
  else
    skip "Virtual environment kept at $VENV_DIR"
  fi
fi

# PID / state files
DWS_STATE="$HOME/.dws"
if [ -d "$DWS_STATE" ]; then
  dry "Remove $DWS_STATE (PID files, process logs)" rm -rf "$DWS_STATE"
  ok "Removed: $DWS_STATE"
fi

# ===========================================================================
# PHASE 4 — RUNTIME DATA (prompts per data type)
# ===========================================================================
log ""
log "═══ Phase 4: Runtime data ═══"

if [ "$OPT_KEEP_DATA" -eq 1 ]; then
  skip "Runtime data preserved (--keep-data)"
elif [ ! -d "$RUNTIME_BASE" ]; then
  skip "Runtime directory not found: $RUNTIME_BASE"
else
  log "Runtime directory: $RUNTIME_BASE"
  RUNTIME_SIZE=$(du -sh "$RUNTIME_BASE" 2>/dev/null | cut -f1)
  log "Total size: $RUNTIME_SIZE"

  # Database
  DB_PATH="$RUNTIME_BASE/database/dws_data.db"
  if [ -f "$DB_PATH" ]; then
    DB_SIZE=$(du -h "$DB_PATH" | cut -f1)
    DB_ROWS=0
    command -v sqlite3 &>/dev/null && \
      DB_ROWS=$(sqlite3 "$DB_PATH" "SELECT count(*) FROM transactions;" 2>/dev/null || echo "?")
    log "Database: $DB_SIZE, $DB_ROWS transaction(s)"
    if prompt_yes "Delete database ($DB_SIZE, $DB_ROWS transactions)?"; then
      # Offer a final backup
      if prompt_yes "Create a final backup before deleting?"; then
        dry "Final backup of database" bash "$SCRIPT_DIR/backup.sh" 2>&1
      fi
      dry "Delete database: $DB_PATH" rm -f "$DB_PATH"
      ok "Database deleted"
    else
      skip "Database kept at $DB_PATH"
    fi
  fi

  # Captures
  CAP_DIR="$RUNTIME_BASE/captures"
  if [ -d "$CAP_DIR" ]; then
    CAP_COUNT=$(find "$CAP_DIR" -type f 2>/dev/null | wc -l)
    CAP_SIZE=$(du -sh "$CAP_DIR" 2>/dev/null | cut -f1)
    log "Captures: $CAP_COUNT file(s), $CAP_SIZE"
    if prompt_yes "Delete all captures ($CAP_COUNT files, $CAP_SIZE)?"; then
      dry "Delete captures: $CAP_DIR" rm -rf "$CAP_DIR"
      ok "Captures deleted"
    else
      skip "Captures kept at $CAP_DIR"
    fi
  fi

  # Pointclouds
  PLY_DIR="$RUNTIME_BASE/pointclouds"
  if [ -d "$PLY_DIR" ]; then
    PLY_COUNT=$(find "$PLY_DIR" -name "*.ply" 2>/dev/null | wc -l)
    PLY_SIZE=$(du -sh "$PLY_DIR" 2>/dev/null | cut -f1)
    log "Pointclouds: $PLY_COUNT .ply file(s), $PLY_SIZE"
    if prompt_yes "Delete all pointclouds ($PLY_COUNT files, $PLY_SIZE)?"; then
      dry "Delete pointclouds: $PLY_DIR" rm -rf "$PLY_DIR"
      ok "Pointclouds deleted"
    else
      skip "Pointclouds kept at $PLY_DIR"
    fi
  fi

  # Calibration
  CAL_DIR="$RUNTIME_BASE/calibration"
  if [ -d "$CAL_DIR" ]; then
    if prompt_yes "Delete calibration files ($CAL_DIR)?"; then
      dry "Delete calibration: $CAL_DIR" rm -rf "$CAL_DIR"
      ok "Calibration files deleted"
    else
      skip "Calibration kept at $CAL_DIR"
    fi
  fi

  # Exports, logs, backups — less sensitive, still prompt
  for subdir in exports logs backups; do
    FULL="$RUNTIME_BASE/$subdir"
    if [ -d "$FULL" ]; then
      SZ=$(du -sh "$FULL" 2>/dev/null | cut -f1)
      if prompt_yes "Delete $subdir/ ($SZ)?"; then
        dry "Delete $FULL" rm -rf "$FULL"
        ok "Deleted: dws_runtime/$subdir"
      else
        skip "Kept: dws_runtime/$subdir"
      fi
    fi
  done

  # Remove runtime root if now empty
  if [ "$OPT_DRY_RUN" -eq 0 ] && [ -d "$RUNTIME_BASE" ]; then
    REMAINING=$(find "$RUNTIME_BASE" -mindepth 1 2>/dev/null | wc -l)
    if [ "$REMAINING" -eq 0 ]; then
      rmdir "$RUNTIME_BASE"
      ok "Runtime root removed (was empty)"
    else
      log "Runtime root kept ($REMAINING item(s) remain): $RUNTIME_BASE"
    fi
  fi
fi

# ===========================================================================
# PHASE 5 — OPTIONAL: REMOVE SYSTEM PACKAGES
# ===========================================================================
log ""
log "═══ Phase 5: System packages (optional) ═══"

log "Note: ROS2 and Intel RealSense SDK are not removed automatically."
log "They may be used by other software on this machine."
log "To remove manually:"
log "  ROS2:        sudo apt remove ros-$( lsb_release -rs | grep -q "24.04" && echo jazzy || echo humble )-*"
log "  RealSense:   sudo apt remove librealsense2-dkms librealsense2-utils librealsense2-dev"

# ===========================================================================
# SUMMARY
# ===========================================================================
log ""
log "═══════════════════════════════════════════"
log "  Uninstall complete"
[ "$OPT_DRY_RUN" -eq 1 ] && log "  (DRY RUN — no changes were made)"
log "  Log: $UNINSTALL_LOG"
log "═══════════════════════════════════════════"
printf "\nUninstall complete.\n"
[ "$OPT_DRY_RUN" -eq 0 ] && printf "Log: %s\n\n" "$UNINSTALL_LOG"
