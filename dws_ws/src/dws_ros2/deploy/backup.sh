#!/bin/bash
# =============================================================================
# DWS System — Backup Script
# =============================================================================
# Creates a timestamped, atomic backup of all DWS runtime data:
#   - SQLite database
#   - Runtime configuration (dws_params.yaml)
#   - Calibration files (.npy, .jpg)
#   - Application parameters and VERSION file
#   - Recent log files
#
# Usage:
#   bash deploy/backup.sh [--out <dir>] [--no-logs] [--quiet]
#
#   --out <dir>   Override backup destination directory
#                 (default: dws_runtime/backups/YYYY-MM-DD_HHMMSS/)
#   --no-logs     Exclude log files from backup (saves space)
#   --quiet       Suppress progress output
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(dirname "$SCRIPT_DIR")"
WS_DIR="$(dirname "$(dirname "$PKG_DIR")")"
REPO_ROOT="$(dirname "$WS_DIR")"

RUNTIME_BASE="$REPO_ROOT/dws_runtime"
CONFIG_FILE="$PKG_DIR/config/dws_params.yaml"
BACKUP_BASE="$RUNTIME_BASE/backups"

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
TIMESTAMP="$(date '+%Y-%m-%d_%H%M%S')"
BACKUP_DIR=""
OPT_NO_LOGS=0
OPT_QUIET=0

_i=1
while [ $_i -le $# ]; do
  arg="${!_i}"
  case "$arg" in
    --out)
      _i=$((_i + 1))
      BACKUP_DIR="${!_i}"
      ;;
    --no-logs) OPT_NO_LOGS=1 ;;
    --quiet)   OPT_QUIET=1 ;;
    -h|--help)
      grep '^#' "$0" | head -14 | sed 's/^# \{0,1\}//'
      exit 0
      ;;
  esac
  _i=$((_i + 1))
done

[ -z "$BACKUP_DIR" ] && BACKUP_DIR="$BACKUP_BASE/$TIMESTAMP"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log() {
  [ "$OPT_QUIET" -eq 0 ] && printf "[%s] %s\n" "$(date '+%H:%M:%S')" "$*"
  printf "[%s] %s\n" "$(date '+%H:%M:%S')" "$*" >> "$BACKUP_DIR/backup.log" 2>/dev/null || true
}
ok()   { log "[OK ] $*"; }
warn() { log "[WARN] $*"; }
fail() { log "[FAIL] $*"; exit 1; }

# ---------------------------------------------------------------------------
# Create backup directory
# ---------------------------------------------------------------------------
mkdir -p "$BACKUP_DIR"
log "DWS Backup started — $TIMESTAMP"
log "Destination: $BACKUP_DIR"

# Write a manifest header
MANIFEST="$BACKUP_DIR/MANIFEST.txt"
{
  echo "DWS Backup Manifest"
  echo "==================="
  echo "Timestamp : $TIMESTAMP"
  echo "Host      : $(hostname)"
  echo "User      : ${USER:-$(id -un)}"
  echo "Repo root : $REPO_ROOT"
  echo ""
  echo "Contents:"
} > "$MANIFEST"

BACKUP_ERRORS=0

# ---------------------------------------------------------------------------
# 1. SQLite database — use .backup for a consistent hot copy
# ---------------------------------------------------------------------------
log "Backing up SQLite database..."
DB_SRC="$RUNTIME_BASE/database/dws_data.db"
DB_DST="$BACKUP_DIR/dws_data.db"

if [ -f "$DB_SRC" ]; then
  if command -v sqlite3 &>/dev/null; then
    sqlite3 "$DB_SRC" ".backup '$DB_DST'" 2>/dev/null
    INTEGRITY=$(sqlite3 "$DB_DST" "PRAGMA integrity_check;" 2>/dev/null | head -1)
    if [ "$INTEGRITY" = "ok" ]; then
      ok "Database: $(du -h "$DB_DST" | cut -f1), integrity: ok"
      echo "  database/dws_data.db  [$(du -h "$DB_DST" | cut -f1)]" >> "$MANIFEST"
    else
      warn "Database backup integrity check: $INTEGRITY"
      BACKUP_ERRORS=$((BACKUP_ERRORS + 1))
    fi
  else
    # Fallback: raw copy (less safe while DB is live, but better than nothing)
    cp "$DB_SRC" "$DB_DST"
    ok "Database: raw copy (sqlite3 CLI not available)"
    echo "  database/dws_data.db  [raw copy]" >> "$MANIFEST"
  fi
else
  warn "Database not found at $DB_SRC — skipping"
  echo "  database/dws_data.db  [SKIPPED — not found]" >> "$MANIFEST"
fi

# Also include any secondary .db files
for extra_db in "$RUNTIME_BASE"/database/*.db; do
  [ "$extra_db" = "$DB_SRC" ] && continue
  [ -f "$extra_db" ] || continue
  base="$(basename "$extra_db")"
  if command -v sqlite3 &>/dev/null; then
    sqlite3 "$extra_db" ".backup '$BACKUP_DIR/$base'" 2>/dev/null
  else
    cp "$extra_db" "$BACKUP_DIR/$base"
  fi
  ok "Extra DB: $base"
  echo "  database/$base  [$(du -h "$BACKUP_DIR/$base" | cut -f1)]" >> "$MANIFEST"
done

# ---------------------------------------------------------------------------
# 2. Runtime configuration
# ---------------------------------------------------------------------------
log "Backing up configuration..."
mkdir -p "$BACKUP_DIR/config"

if [ -f "$CONFIG_FILE" ]; then
  cp "$CONFIG_FILE" "$BACKUP_DIR/config/dws_params.yaml"
  ok "Config: dws_params.yaml"
  echo "  config/dws_params.yaml" >> "$MANIFEST"
else
  warn "Config not found at $CONFIG_FILE"
fi

# package.xml for version info
PACKAGE_XML="$PKG_DIR/package.xml"
if [ -f "$PACKAGE_XML" ]; then
  cp "$PACKAGE_XML" "$BACKUP_DIR/config/package.xml"
  echo "  config/package.xml" >> "$MANIFEST"
fi

# VERSION file
if [ -f "$REPO_ROOT/VERSION" ]; then
  cp "$REPO_ROOT/VERSION" "$BACKUP_DIR/config/VERSION"
  echo "  config/VERSION" >> "$MANIFEST"
fi

# ---------------------------------------------------------------------------
# 3. Calibration files
# ---------------------------------------------------------------------------
log "Backing up calibration files..."
mkdir -p "$BACKUP_DIR/calibration"

CAL_DIR="$RUNTIME_BASE/calibration"
CAL_FILES=(stable_empty_depth.npy noise_map.npy system_geometry.npy calibration_result.jpg)
CAL_FOUND=0

for cal in "${CAL_FILES[@]}"; do
  if [ -f "$CAL_DIR/$cal" ]; then
    cp "$CAL_DIR/$cal" "$BACKUP_DIR/calibration/$cal"
    ok "Calibration: $cal ($(du -h "$CAL_DIR/$cal" | cut -f1))"
    echo "  calibration/$cal  [$(du -h "$CAL_DIR/$cal" | cut -f1)]" >> "$MANIFEST"
    CAL_FOUND=$((CAL_FOUND + 1))
  else
    warn "Calibration file not found: $cal"
  fi
done

if [ "$CAL_FOUND" -eq 0 ]; then
  warn "No calibration files found — station may need recalibration after restore"
fi

# ---------------------------------------------------------------------------
# 4. Log files (recent only — last 7 days)
# ---------------------------------------------------------------------------
if [ "$OPT_NO_LOGS" -eq 0 ]; then
  log "Backing up recent logs..."
  mkdir -p "$BACKUP_DIR/logs"

  # Runtime logs
  if [ -d "$RUNTIME_BASE/logs" ]; then
    find "$RUNTIME_BASE/logs" -type f -mtime -7 | while read -r f; do
      rel="${f#$RUNTIME_BASE/logs/}"
      mkdir -p "$BACKUP_DIR/logs/runtime/$(dirname "$rel")"
      cp "$f" "$BACKUP_DIR/logs/runtime/$rel"
    done
    LOG_COUNT=$(find "$BACKUP_DIR/logs/runtime" -type f 2>/dev/null | wc -l)
    ok "Runtime logs: $LOG_COUNT file(s)"
    echo "  logs/runtime/  [$LOG_COUNT file(s), last 7 days]" >> "$MANIFEST"
  fi

  # DWS process logs (~/.dws/logs/)
  DWS_LOGS="$HOME/.dws/logs"
  if [ -d "$DWS_LOGS" ]; then
    mkdir -p "$BACKUP_DIR/logs/process"
    for log_file in backend.log api.log hmi.log; do
      if [ -f "$DWS_LOGS/$log_file" ]; then
        # Tail last 5000 lines to keep backup size manageable
        tail -5000 "$DWS_LOGS/$log_file" > "$BACKUP_DIR/logs/process/$log_file"
        ok "Process log: $log_file (last 5000 lines)"
        echo "  logs/process/$log_file  [last 5000 lines]" >> "$MANIFEST"
      fi
    done
  fi

  # install.log
  if [ -f "$REPO_ROOT/install.log" ]; then
    cp "$REPO_ROOT/install.log" "$BACKUP_DIR/logs/install.log"
    echo "  logs/install.log" >> "$MANIFEST"
  fi
else
  log "Log backup skipped (--no-logs)"
fi

# ---------------------------------------------------------------------------
# 5. Checksum manifest (computed before manifest footer is written)
# ---------------------------------------------------------------------------
log "Computing checksums..."
CHECKSUM_FILE="$BACKUP_DIR/checksums.sha256"
find "$BACKUP_DIR" -type f \
  ! -name "checksums.sha256" \
  ! -name "backup.log" \
  ! -name "MANIFEST.txt" \
  -exec sha256sum {} \; \
  | sed "s|$BACKUP_DIR/||" \
  > "$CHECKSUM_FILE"

CHECKSUM_COUNT=$(wc -l < "$CHECKSUM_FILE")
ok "Checksums: $CHECKSUM_COUNT file(s) hashed to checksums.sha256"

# ---------------------------------------------------------------------------
# 6. Finalise manifest
# ---------------------------------------------------------------------------
{
  echo ""
  echo "Errors during backup: $BACKUP_ERRORS"
  echo "Total size: $(du -sh "$BACKUP_DIR" | cut -f1)"
  echo "Checksums: checksums.sha256"
} >> "$MANIFEST"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
BACKUP_SIZE=$(du -sh "$BACKUP_DIR" | cut -f1)
log ""
log "═══════════════════════════════════════════"
log "  Backup complete"
log "  Location : $BACKUP_DIR"
log "  Size     : $BACKUP_SIZE"
[ "$BACKUP_ERRORS" -gt 0 ] && log "  Warnings : $BACKUP_ERRORS" || log "  Errors   : 0"
log "═══════════════════════════════════════════"

if [ "$OPT_QUIET" -eq 0 ]; then
  printf "\nBackup created: %s\n" "$BACKUP_DIR"
  printf "To restore: bash deploy/restore.sh --from %s\n\n" "$BACKUP_DIR"
fi

# Return the backup path on stdout for scripting
[ "$OPT_QUIET" -eq 1 ] && printf "%s\n" "$BACKUP_DIR"

exit 0
