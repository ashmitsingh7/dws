#!/bin/bash
# =============================================================================
# DWS System — Safe Update Script
# =============================================================================
# Safely updates an existing DWS installation from the local git repository.
#
# Workflow:
#   1. Pre-flight checks
#   2. Stop services
#   3. Backup current state
#   4. Pull / apply update
#   5. Rebuild workspace
#   6. Restart services
#   7. Verify
#   8. Rollback if verification fails
#
# Usage:
#   bash deploy/update.sh [--branch <branch>] [--tag <tag>] [--no-pull]
#                         [--force] [--dry-run]
#
#   --branch <b>  Git branch to update to (default: current branch)
#   --tag <t>     Git tag to update to
#   --no-pull     Skip git pull (apply local uncommitted changes only)
#   --force       Skip confirmation prompts
#   --dry-run     Show what would happen; make no changes
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
VENV_PIP="$VENV_DIR/bin/pip"
RUNTIME_BASE="$REPO_ROOT/dws_runtime"
REQUIREMENTS_TXT="$WS_DIR/requirements.txt"

_UBUNTU_RELEASE=$(lsb_release -rs 2>/dev/null || echo "unknown")
case "$_UBUNTU_RELEASE" in
  24.04) ROS_DISTRO="jazzy"  ;;
  22.04) ROS_DISTRO="humble" ;;
  *)     ROS_DISTRO="jazzy"  ;;
esac
ROS_SETUP_FILE="/opt/ros/$ROS_DISTRO/setup.bash"

SVC_NAME="dws@$USER.service"

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
GIT_BRANCH=""
GIT_TAG=""
OPT_NO_PULL=0
OPT_FORCE=0
OPT_DRY_RUN=0

_i=1
while [ $_i -le $# ]; do
  arg="${!_i}"
  case "$arg" in
    --branch)
      _i=$((_i + 1))
      GIT_BRANCH="${!_i}"
      ;;
    --tag)
      _i=$((_i + 1))
      GIT_TAG="${!_i}"
      ;;
    --no-pull) OPT_NO_PULL=1 ;;
    --force)   OPT_FORCE=1 ;;
    --dry-run) OPT_DRY_RUN=1 ;;
    -h|--help)
      grep '^#' "$0" | head -18 | sed 's/^# \{0,1\}//'
      exit 0
      ;;
  esac
  _i=$((_i + 1))
done

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
UPDATE_LOG="$REPO_ROOT/update_$(date '+%Y%m%d_%H%M%S').log"
ROLLBACK_NEEDED=0
BACKUP_DIR=""
PRE_UPDATE_COMMIT=""
SERVICE_WAS_RUNNING=0

log()  { printf "[%s] %s\n" "$(date '+%H:%M:%S')" "$*" | tee -a "$UPDATE_LOG"; }
ok()   { log "[OK ] $*"; }
warn() { log "[WARN] $*"; }
fail() { log "[FAIL] $*"; _rollback; exit 1; }

_rollback() {
  if [ "$ROLLBACK_NEEDED" -eq 0 ]; then return; fi
  log ""
  log "══════════ ROLLBACK TRIGGERED ══════════"

  # Restore git state
  if [ -n "$PRE_UPDATE_COMMIT" ] && [ -d "$REPO_ROOT/.git" ]; then
    log "Resetting git to $PRE_UPDATE_COMMIT ..."
    git -C "$REPO_ROOT" reset --hard "$PRE_UPDATE_COMMIT" 2>&1 | tee -a "$UPDATE_LOG" || true
    ok "Git reset to pre-update commit"
  fi

  # Rebuild workspace with old code
  log "Rebuilding workspace with rolled-back code..."
  (
    # shellcheck disable=SC1090
    source "$ROS_SETUP_FILE" 2>/dev/null || true
    cd "$WS_DIR"
    colcon build --packages-select dws_ros2 2>&1 | tail -10
  ) | tee -a "$UPDATE_LOG" || warn "Rollback rebuild failed — manual intervention required"

  # Restore database from pre-update backup
  if [ -n "$BACKUP_DIR" ] && [ -f "$BACKUP_DIR/dws_data.db" ]; then
    log "Restoring database from backup..."
    bash "$SCRIPT_DIR/restore.sh" \
      --from "$BACKUP_DIR" \
      --no-service \
      --force \
      2>&1 | tee -a "$UPDATE_LOG" || warn "Database restore failed"
  fi

  # Restart service
  if [ "$SERVICE_WAS_RUNNING" -eq 1 ]; then
    log "Restarting service after rollback..."
    sudo systemctl start "$SVC_NAME" 2>/dev/null || \
      warn "Could not restart service — start manually: sudo systemctl start $SVC_NAME"
  fi

  log "Rollback complete. Update log: $UPDATE_LOG"
}

# ---------------------------------------------------------------------------
# START
# ---------------------------------------------------------------------------
log "DWS Update starting — $(date '+%Y-%m-%d %H:%M:%S')"
log "Update log : $UPDATE_LOG"

if [ "$OPT_DRY_RUN" -eq 1 ]; then
  log "DRY RUN MODE — no changes will be made"
fi

# ===========================================================================
# PHASE 1 — PRE-FLIGHT
# ===========================================================================
log ""
log "═══ Phase 1: Pre-flight checks ═══"

# Must be in a git repo
if [ ! -d "$REPO_ROOT/.git" ]; then
  log "[WARN] Not a git repository — only local rebuild will be performed"
  OPT_NO_PULL=1
fi

if [ -d "$REPO_ROOT/.git" ]; then
  PRE_UPDATE_COMMIT=$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo "")
  PRE_UPDATE_BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
  ok "Current commit: $PRE_UPDATE_COMMIT ($PRE_UPDATE_BRANCH)"
fi

# Verify venv and workspace exist
[ -f "$VENV_PIP" ] || fail "Virtual environment not found at $VENV_DIR — run install.sh first"
[ -f "$ROS_SETUP_FILE" ] || fail "ROS2 setup not found at $ROS_SETUP_FILE — run install.sh first"
ok "Virtual environment: $VENV_DIR"
ok "ROS2: $ROS_SETUP_FILE"

# Disk space (need at least 2GB for rebuild)
FREE_KB=$(df -k "$WS_DIR" | tail -1 | awk '{print $4}')
FREE_GB=$(echo "scale=1; $FREE_KB / 1048576" | bc 2>/dev/null || echo "?")
if [ "$FREE_KB" -lt 2097152 ] 2>/dev/null; then
  warn "Low disk space: ${FREE_GB}GB free — update may fail"
else
  ok "Disk space: ${FREE_GB}GB free"
fi

# ===========================================================================
# PHASE 2 — CONFIRMATION
# ===========================================================================
if [ "$OPT_FORCE" -eq 0 ] && [ "$OPT_DRY_RUN" -eq 0 ]; then
  printf "\n"
  printf "  DWS Update\n"
  printf "  Current: %s (%s)\n" "$PRE_UPDATE_COMMIT" "$PRE_UPDATE_BRANCH"
  if [ -n "$GIT_TAG" ]; then
    printf "  Target:  tag %s\n" "$GIT_TAG"
  elif [ -n "$GIT_BRANCH" ]; then
    printf "  Target:  branch %s\n" "$GIT_BRANCH"
  elif [ "$OPT_NO_PULL" -eq 0 ]; then
    printf "  Target:  latest on %s\n" "$PRE_UPDATE_BRANCH"
  else
    printf "  Target:  local rebuild (no pull)\n"
  fi
  printf "\n"
  printf "  Continue? [y/N] "
  read -r CONFIRM
  case "$CONFIRM" in
    y|Y|yes|YES) ok "Confirmed by user" ;;
    *) log "Update cancelled."; exit 0 ;;
  esac
fi

[ "$OPT_DRY_RUN" -eq 1 ] && { log "DRY RUN — stopping here."; exit 0; }

# ===========================================================================
# PHASE 3 — STOP SERVICE
# ===========================================================================
log ""
log "═══ Phase 3: Stop service ═══"

if systemctl is-active --quiet "$SVC_NAME" 2>/dev/null; then
  SERVICE_WAS_RUNNING=1
  log "Stopping $SVC_NAME ..."
  sudo systemctl stop "$SVC_NAME" 2>&1 | tee -a "$UPDATE_LOG"
  sleep 3
  ok "Service stopped"
else
  ok "Service was not running"
fi

# ===========================================================================
# PHASE 4 — BACKUP
# ===========================================================================
log ""
log "═══ Phase 4: Backup current installation ═══"

ROLLBACK_NEEDED=1   # from this point, any failure triggers rollback

BACKUP_DIR=$(bash "$SCRIPT_DIR/backup.sh" --quiet 2>&1 | tail -1)
if [ -d "$BACKUP_DIR" ]; then
  ok "Backup created: $BACKUP_DIR"
else
  # Non-quiet mode — backup.sh printed its own output, parse from log
  BACKUP_DIR=$(bash "$SCRIPT_DIR/backup.sh" 2>&1 | tee -a "$UPDATE_LOG" | \
    grep "Destination:" | head -1 | awk '{print $2}' || echo "")
  [ -d "$BACKUP_DIR" ] && ok "Backup created: $BACKUP_DIR" || \
    warn "Could not determine backup path — rollback to git only if needed"
fi

# ===========================================================================
# PHASE 5 — APPLY UPDATE
# ===========================================================================
log ""
log "═══ Phase 5: Apply update ═══"

if [ "$OPT_NO_PULL" -eq 0 ] && [ -d "$REPO_ROOT/.git" ]; then
  if [ -n "$GIT_TAG" ]; then
    log "Fetching and checking out tag: $GIT_TAG"
    git -C "$REPO_ROOT" fetch --tags 2>&1 | tee -a "$UPDATE_LOG"
    git -C "$REPO_ROOT" checkout "$GIT_TAG" 2>&1 | tee -a "$UPDATE_LOG"
  elif [ -n "$GIT_BRANCH" ]; then
    log "Switching to branch: $GIT_BRANCH"
    git -C "$REPO_ROOT" fetch origin 2>&1 | tee -a "$UPDATE_LOG"
    git -C "$REPO_ROOT" checkout "$GIT_BRANCH" 2>&1 | tee -a "$UPDATE_LOG"
    git -C "$REPO_ROOT" pull origin "$GIT_BRANCH" 2>&1 | tee -a "$UPDATE_LOG"
  else
    log "Pulling latest on $PRE_UPDATE_BRANCH ..."
    git -C "$REPO_ROOT" pull 2>&1 | tee -a "$UPDATE_LOG"
  fi

  POST_UPDATE_COMMIT=$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo "unknown")
  ok "Updated to: $POST_UPDATE_COMMIT"
else
  ok "Git pull skipped (--no-pull or not a git repo)"
fi

# ===========================================================================
# PHASE 6 — UPDATE PYTHON DEPENDENCIES
# ===========================================================================
log ""
log "═══ Phase 6: Update Python dependencies ═══"

if [ -f "$REQUIREMENTS_TXT" ]; then
  log "Running pip install -r requirements.txt ..."
  "$VENV_PIP" install \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    -r "$REQUIREMENTS_TXT" \
    2>&1 | tee -a "$UPDATE_LOG" || fail "pip install failed"
  ok "Python dependencies updated"
else
  warn "requirements.txt not found — skipping pip update"
fi

# ===========================================================================
# PHASE 7 — REBUILD WORKSPACE
# ===========================================================================
log ""
log "═══ Phase 7: Rebuild ROS2 workspace ═══"

log "Running colcon build..."
(
  # shellcheck disable=SC1090
  source "$ROS_SETUP_FILE"
  cd "$WS_DIR"
  colcon build --packages-select dws_ros2 2>&1
) | tee -a "$UPDATE_LOG" || fail "colcon build failed"

WS_SETUP="$WS_DIR/install/setup.bash"
[ -f "$WS_SETUP" ] || fail "Workspace build produced no install/setup.bash"
ok "Workspace rebuilt: $WS_SETUP"

# ===========================================================================
# PHASE 8 — RESTART SERVICE
# ===========================================================================
log ""
log "═══ Phase 8: Restart service ═══"

if [ "$SERVICE_WAS_RUNNING" -eq 1 ]; then
  log "Starting $SVC_NAME ..."
  sudo systemctl start "$SVC_NAME" 2>&1 | tee -a "$UPDATE_LOG"
  sleep 5   # give processes time to bind ports
  if systemctl is-active --quiet "$SVC_NAME" 2>/dev/null; then
    ok "Service started successfully"
  else
    fail "Service failed to start after update"
  fi
else
  log "Service was not running before update — not starting"
  log "Start manually: sudo systemctl start $SVC_NAME"
fi

# ===========================================================================
# PHASE 9 — VERIFY
# ===========================================================================
log ""
log "═══ Phase 9: Verify updated installation ═══"

if bash "$SCRIPT_DIR/verify_install.sh" --quiet 2>&1 | tee -a "$UPDATE_LOG"; then
  ok "Verification passed"
  ROLLBACK_NEEDED=0   # update succeeded — do not rollback on exit
else
  fail "Verification failed after update — initiating rollback"
fi

# ===========================================================================
# SUMMARY
# ===========================================================================
log ""
log "═══════════════════════════════════════════"
log "  Update complete — SUCCESS"
log "  Previous : $PRE_UPDATE_COMMIT"
[ -n "$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo "")" ] && \
  log "  Current  : $(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null)"
log "  Backup   : $BACKUP_DIR"
log "  Log      : $UPDATE_LOG"
log "═══════════════════════════════════════════"
printf "\nUpdate successful. Log: %s\n\n" "$UPDATE_LOG"
