#!/bin/bash
# =============================================================================
# DWS System — Restore Script
# =============================================================================
# Restores DWS runtime data from a backup created by deploy/backup.sh.
# Validates the backup before touching any live data.
#
# Usage:
#   bash deploy/restore.sh --from <backup-dir> [--no-service] [--dry-run]
#
#   --from <dir>   Path to the backup directory (required)
#   --no-service   Do not attempt to stop/start the systemd service
#   --dry-run      Validate and show what would be restored; make no changes
#   --force        Skip confirmation prompts (non-interactive / CI use)
# =============================================================================

set -euo pipefail

# Ensure USER is set even in sudo/non-login environments
USER="${USER:-$(id -un)}"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(dirname "$SCRIPT_DIR")"
WS_DIR="$(dirname "$(dirname "$PKG_DIR")")"
REPO_ROOT="$(dirname "$WS_DIR")"

RUNTIME_BASE="$REPO_ROOT/dws_runtime"
CONFIG_FILE="$PKG_DIR/config/dws_params.yaml"

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
BACKUP_DIR=""
OPT_NO_SERVICE=0
OPT_DRY_RUN=0
OPT_FORCE=0

_i=1
while [ $_i -le $# ]; do
  arg="${!_i}"
  case "$arg" in
    --from)
      _i=$((_i + 1))
      BACKUP_DIR="${!_i}"
      ;;
    --no-service) OPT_NO_SERVICE=1 ;;
    --dry-run)    OPT_DRY_RUN=1 ;;
    --force)      OPT_FORCE=1 ;;
    -h|--help)
      grep '^#' "$0" | head -14 | sed 's/^# \{0,1\}//'
      exit 0
      ;;
  esac
  _i=$((_i + 1))
done

if [ -z "$BACKUP_DIR" ]; then
  printf "Error: --from <backup-dir> is required.\n" >&2
  printf "Usage: bash deploy/restore.sh --from <backup-dir>\n" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
RESTORE_LOG="$REPO_ROOT/restore_$(date '+%Y%m%d_%H%M%S').log"
log()  { printf "[%s] %s\n" "$(date '+%H:%M:%S')" "$*" | tee -a "$RESTORE_LOG"; }
ok()   { log "[OK ] $*"; }
warn() { log "[WARN] $*"; }
fail() { log "[FAIL] $*"; exit 1; }

if [ "$OPT_DRY_RUN" -eq 1 ]; then
  log "DRY RUN MODE — no files will be modified"
fi

log "DWS Restore starting"
log "Backup source : $BACKUP_DIR"
log "Restore log   : $RESTORE_LOG"

# ===========================================================================
# PHASE 1 — VALIDATE BACKUP
# ===========================================================================
log ""
log "═══ Phase 1: Validate backup ═══"

# Backup directory must exist
[ -d "$BACKUP_DIR" ] || fail "Backup directory not found: $BACKUP_DIR"
ok "Backup directory exists"

# Required files
REQUIRED=(
  "MANIFEST.txt"
  "checksums.sha256"
)
for f in "${REQUIRED[@]}"; do
  [ -f "$BACKUP_DIR/$f" ] || fail "Backup is missing: $f — backup may be corrupt or incomplete"
  ok "Found: $f"
done

# Verify checksums
log "Verifying checksums..."
CHECKSUM_ERRORS=0
while IFS= read -r line; do
  expected_hash="${line%% *}"
  rel_path="${line##* }"

  # Remove leading ./ if any
  rel_path="${rel_path#./}"

  # Skip metadata files that may be updated after checksums are computed
  case "$rel_path" in
    MANIFEST.txt|backup.log) continue ;;
  esac

  full_path="$BACKUP_DIR/$rel_path"

  if [ -f "$full_path" ]; then
    actual_hash=$(sha256sum "$full_path" | cut -d' ' -f1)
    if [ "$actual_hash" != "$expected_hash" ]; then
      warn "Checksum MISMATCH: $rel_path"
      CHECKSUM_ERRORS=$((CHECKSUM_ERRORS + 1))
    fi
  else
    warn "File in checksum list not present: $rel_path"
    CHECKSUM_ERRORS=$((CHECKSUM_ERRORS + 1))
  fi
done < "$BACKUP_DIR/checksums.sha256"

if [ "$CHECKSUM_ERRORS" -gt 0 ]; then
  fail "$CHECKSUM_ERRORS checksum error(s) — backup is corrupt. Aborting."
fi
ok "All checksums verified"

# Validate database backup if present
DB_BACKUP="$BACKUP_DIR/dws_data.db"
if [ -f "$DB_BACKUP" ]; then
  if command -v sqlite3 &>/dev/null; then
    INTEGRITY=$(sqlite3 "$DB_BACKUP" "PRAGMA integrity_check;" 2>/dev/null | head -1)
    if [ "$INTEGRITY" = "ok" ]; then
      ROW_COUNT=$(sqlite3 "$DB_BACKUP" "SELECT count(*) FROM transactions;" 2>/dev/null || echo "?")
      ok "Database backup integrity: ok ($ROW_COUNT transaction rows)"
    else
      fail "Database backup integrity check failed: $INTEGRITY"
    fi
  else
    ok "Database backup present (sqlite3 CLI unavailable for deep check)"
  fi
else
  warn "No database backup in this archive"
fi

# Show what will be restored
log ""
log "Backup manifest:"
cat "$BACKUP_DIR/MANIFEST.txt" | while IFS= read -r line; do log "  $line"; done

# ===========================================================================
# PHASE 2 — CONFIRMATION
# ===========================================================================
log ""
log "═══ Phase 2: Confirmation ═══"

if [ "$OPT_DRY_RUN" -eq 1 ]; then
  log "DRY RUN — stopping here. No files have been changed."
  exit 0
fi

if [ "$OPT_FORCE" -eq 0 ]; then
  printf "\n"
  printf "  This will OVERWRITE live DWS data with the backup from:\n"
  printf "    %s\n\n" "$BACKUP_DIR"
  printf "  Data that will be replaced:\n"
  [ -f "$DB_BACKUP" ]                          && printf "    - SQLite database\n"
  [ -f "$BACKUP_DIR/config/dws_params.yaml" ]  && printf "    - dws_params.yaml config\n"
  [ -d "$BACKUP_DIR/calibration" ]             && printf "    - Calibration files\n"
  printf "\n"
  printf "  Continue? [y/N] "
  read -r CONFIRM
  case "$CONFIRM" in
    y|Y|yes|YES) ok "Confirmed by user" ;;
    *) log "Restore cancelled by user."; exit 0 ;;
  esac
fi

# ===========================================================================
# PHASE 3 — STOP SERVICE
# ===========================================================================
log ""
log "═══ Phase 3: Stop DWS service ═══"

SVC_NAME="dws@$USER.service"
SERVICE_WAS_RUNNING=0

if [ "$OPT_NO_SERVICE" -eq 0 ]; then
  if systemctl is-active --quiet "$SVC_NAME" 2>/dev/null; then
    SERVICE_WAS_RUNNING=1
    log "Stopping $SVC_NAME ..."
    if [ "$(id -u)" -eq 0 ]; then
      systemctl stop "$SVC_NAME"
    else
      sudo systemctl stop "$SVC_NAME"
    fi
    ok "Service stopped"
    sleep 2   # allow DB file handles to close
  else
    ok "Service not running — no stop needed"
  fi
else
  warn "Service management skipped (--no-service) — ensure DWS is not running before restore"
fi

# ===========================================================================
# PHASE 4 — SAFETY BACKUP OF CURRENT LIVE DATA
# ===========================================================================
log ""
log "═══ Phase 4: Safety snapshot of current live data ═══"

SAFETY_BACKUP="$RUNTIME_BASE/backups/pre-restore_$(date '+%Y-%m-%d_%H%M%S')"
mkdir -p "$SAFETY_BACKUP"

if [ -f "$RUNTIME_BASE/database/dws_data.db" ]; then
  if command -v sqlite3 &>/dev/null; then
    sqlite3 "$RUNTIME_BASE/database/dws_data.db" \
      ".backup '$SAFETY_BACKUP/dws_data.db'" 2>/dev/null || \
      cp "$RUNTIME_BASE/database/dws_data.db" "$SAFETY_BACKUP/dws_data.db"
  else
    cp "$RUNTIME_BASE/database/dws_data.db" "$SAFETY_BACKUP/dws_data.db"
  fi
  ok "Safety backup of live database: $SAFETY_BACKUP/dws_data.db"
fi
ok "Safety snapshot: $SAFETY_BACKUP"

# ===========================================================================
# PHASE 5 — RESTORE FILES
# ===========================================================================
log ""
log "═══ Phase 5: Restore files ═══"

_copy() {
  local src="$1"
  local dst="$2"
  [ -f "$src" ] || return 0
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  ok "Restored: $(basename "$dst")"
}

# Database
if [ -f "$DB_BACKUP" ]; then
  mkdir -p "$RUNTIME_BASE/database"
  if command -v sqlite3 &>/dev/null; then
    # Restore via sqlite3 .restore for a clean copy
    sqlite3 "$RUNTIME_BASE/database/dws_data.db" \
      ".restore '$DB_BACKUP'" 2>/dev/null || \
      cp "$DB_BACKUP" "$RUNTIME_BASE/database/dws_data.db"
  else
    cp "$DB_BACKUP" "$RUNTIME_BASE/database/dws_data.db"
  fi
  ok "Database restored"
fi

# Configuration
_copy "$BACKUP_DIR/config/dws_params.yaml" "$CONFIG_FILE"

# Calibration
mkdir -p "$RUNTIME_BASE/calibration"
for cal in stable_empty_depth.npy noise_map.npy system_geometry.npy calibration_result.jpg; do
  _copy "$BACKUP_DIR/calibration/$cal" "$RUNTIME_BASE/calibration/$cal"
done

# VERSION (informational only)
_copy "$BACKUP_DIR/config/VERSION" "$REPO_ROOT/VERSION"

# ===========================================================================
# PHASE 6 — VERIFY RESTORED DATA
# ===========================================================================
log ""
log "═══ Phase 6: Verify restored data ═══"

VERIFY_ERRORS=0

if [ -f "$RUNTIME_BASE/database/dws_data.db" ]; then
  if command -v sqlite3 &>/dev/null; then
    INTEGRITY=$(sqlite3 "$RUNTIME_BASE/database/dws_data.db" "PRAGMA integrity_check;" 2>/dev/null | head -1)
    if [ "$INTEGRITY" = "ok" ]; then
      ok "Restored database integrity: ok"
    else
      warn "Restored database integrity: $INTEGRITY"
      VERIFY_ERRORS=$((VERIFY_ERRORS + 1))
    fi
  fi
fi

for cal in stable_empty_depth.npy noise_map.npy system_geometry.npy; do
  if [ -f "$RUNTIME_BASE/calibration/$cal" ]; then
    ok "Calibration: $cal present"
  else
    warn "Calibration: $cal still missing after restore"
    VERIFY_ERRORS=$((VERIFY_ERRORS + 1))
  fi
done

if [ "$VERIFY_ERRORS" -gt 0 ]; then
  warn "$VERIFY_ERRORS verification issue(s) after restore — check $RESTORE_LOG"
fi

# ===========================================================================
# PHASE 7 — RESTART SERVICE
# ===========================================================================
log ""
log "═══ Phase 7: Restart service ═══"

if [ "$OPT_NO_SERVICE" -eq 0 ] && [ "$SERVICE_WAS_RUNNING" -eq 1 ]; then
  log "Restarting $SVC_NAME ..."
  if [ "$(id -u)" -eq 0 ]; then
    systemctl start "$SVC_NAME"
  else
    sudo systemctl start "$SVC_NAME"
  fi
  sleep 3
  if systemctl is-active --quiet "$SVC_NAME" 2>/dev/null; then
    ok "Service restarted and active"
  else
    warn "Service did not start — check: journalctl -u $SVC_NAME -n 30"
  fi
else
  log "Service restart skipped"
  log "Start manually: sudo systemctl start $SVC_NAME"
fi

# ===========================================================================
# SUMMARY
# ===========================================================================
log ""
log "═══════════════════════════════════════════"
log "  Restore complete"
log "  From     : $BACKUP_DIR"
log "  Safety   : $SAFETY_BACKUP"
log "  Log      : $RESTORE_LOG"
[ "$VERIFY_ERRORS" -eq 0 ] && log "  Result   : SUCCESS" || log "  Result   : WARNINGS ($VERIFY_ERRORS)"
log "═══════════════════════════════════════════"
printf "\nRestore complete. Log: %s\n\n" "$RESTORE_LOG"
