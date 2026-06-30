#!/bin/bash
# Thin wrapper: deploy/stop.sh == deploy/run.sh stop
# Kept as a separate file because the handover package layout
# (DWS_System_v1.0/Software/) names run.sh and stop.sh separately.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/run.sh" stop
