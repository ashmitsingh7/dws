# DWS System — Deployment & Recovery Guide

**System:** DWS v1.0 Build 005.1  
**Applies to:** Ubuntu 22.04 LTS / 24.04 LTS · x86_64

---

## Table of Contents

1. [Quick reference — recovery commands](#1-quick-reference)
2. [Failed installation](#2-failed-installation)
3. [Failed update](#3-failed-update)
4. [Camera replacement](#4-camera-replacement-intel-realsense)
5. [Scale replacement](#5-scale-replacement)
6. [Database corruption](#6-database-corruption)
7. [Lost calibration](#7-lost-calibration)
8. [Manual rollback](#8-manual-rollback)
9. [Full station recovery](#9-full-station-recovery)

---

## 1. Quick reference

| Situation | Command |
|-----------|---------|
| Verify all subsystems | `bash deploy/verify_install.sh` |
| Generate diagnostic report | `bash deploy/diagnostics.sh` |
| Create a backup now | `bash deploy/backup.sh` |
| Restore from backup | `bash deploy/restore.sh --from dws_runtime/backups/<timestamp>` |
| Safe update | `bash deploy/update.sh` |
| Uninstall | `bash deploy/uninstall.sh` |
| Service status | `systemctl status dws@$USER.service` |
| Tail live logs | `journalctl -u dws@$USER.service -f` |
| Manual start | `sudo systemctl start dws@$USER.service` |
| Manual stop | `sudo systemctl stop dws@$USER.service` |
| List backups | `ls -lt dws_runtime/backups/` |

---

## 2. Failed installation

### Symptom
`sudo bash deploy/install.sh` exits with a `[FAIL]` line before completing.

### Step 1 — Identify the failed step

```bash
grep -E "\[FAIL\]|\[WARN\]" install.log
```

The `[FAIL]` line names the step that broke. Every step is idempotent — completed steps are skipped on re-run.

### Step 2 — Fix and re-run

Re-running the installer after fixing the root cause is always safe:

```bash
sudo bash deploy/install.sh
```

### Common root causes and fixes

**"apt-get update failed" / "Network unreachable"**

```bash
# Check connectivity
ping -c 2 packages.ros.org
ping -c 2 librealsense.intel.com

# If on a proxy, export it before re-running
export http_proxy=http://proxy.example.com:3128
export https_proxy=http://proxy.example.com:3128
sudo -E bash deploy/install.sh
```

**"librealsense2-dkms failed to install"** (DKMS kernel module build error)

```bash
# Check kernel headers are present for the running kernel
sudo apt-get install -y linux-headers-$(uname -r)
# Then re-run
sudo bash deploy/install.sh
```

**"colcon build failed"** (compilation error)

```bash
cd ~/dws_finalv7/dws_ws
source /opt/ros/jazzy/setup.bash    # or humble
colcon build --packages-select dws_ros2 --event-handlers console_direct+
# Read the first ERROR line for the actual cause
```

**"N package import(s) failed"** after pip install

```bash
source ~/dws_finalv7/dws_ws/.venv/bin/activate
pip install --force-reinstall -r ~/dws_finalv7/dws_ws/requirements.txt
```

**"SUDO_USER is not set"** — script was run as root directly, not via sudo

```bash
# Always use:
sudo bash deploy/install.sh
# Not: su root && bash deploy/install.sh
```

**Disk full mid-install**

```bash
df -h /
# Clear space, then:
sudo bash deploy/install.sh
```

---

## 3. Failed update

### Symptom
`bash deploy/update.sh` fails or the service does not start after update.

`update.sh` automatically triggers a rollback when verification fails. The rollback:
- Resets git to the pre-update commit
- Rebuilds the workspace with the old code
- Restores the database from the pre-update backup
- Restarts the service

### Manual rollback after a failed update

If `update.sh`'s automatic rollback itself fails, perform these steps manually:

**Step 1 — Stop the service**

```bash
sudo systemctl stop dws@$USER.service
```

**Step 2 — Reset git to the last known good commit**

```bash
cd ~/dws_finalv7
git log --oneline -10          # find the last good commit hash
git reset --hard <COMMIT_HASH>
```

**Step 3 — Restore the database**

```bash
ls -lt ~/dws_finalv7/dws_runtime/backups/     # find pre-update backup
bash deploy/restore.sh --from dws_runtime/backups/<pre-update-timestamp> --force
```

**Step 4 — Rebuild and restart**

```bash
source /opt/ros/jazzy/setup.bash
cd ~/dws_finalv7/dws_ws
colcon build --packages-select dws_ros2
sudo systemctl start dws@$USER.service
bash deploy/verify_install.sh
```

---

## 4. Camera replacement (Intel RealSense)

Use this procedure when swapping a faulty RealSense camera for a new unit.

### Step 1 — Stop the service

```bash
sudo systemctl stop dws@$USER.service
```

### Step 2 — Remove the old camera, connect the new one

Connect the replacement on the same USB 3.0 port. USB 3.0 is required — using USB 2.0 will cause the camera to fail to initialise.

### Step 3 — Verify the new camera is detected

```bash
rs-enumerate-devices
# Expected output includes name, serial number, firmware version
```

If no devices are listed:

```bash
# Reload udev rules (needed if camera was connected before rules were installed)
sudo udevadm control --reload-rules
sudo udevadm trigger
# Try again
rs-enumerate-devices
```

### Step 4 — Run re-calibration

Replacing the camera invalidates all calibration files. The new camera has a different intrinsic profile and physical position will vary slightly.

```bash
# Remove stale calibration files
rm ~/dws_finalv7/dws_runtime/calibration/stable_empty_depth.npy
rm ~/dws_finalv7/dws_runtime/calibration/noise_map.npy
rm ~/dws_finalv7/dws_runtime/calibration/system_geometry.npy
```

Start the service and run the calibration wizard:

```bash
sudo systemctl start dws@$USER.service
# Open http://localhost:8080 → Settings → Calibrate
```

### Step 5 — Verify

```bash
bash deploy/verify_install.sh
```

The `RealSense camera` line must show `PASS` with the new device's name and serial number.

---

## 5. Scale replacement

Use this procedure when swapping a faulty serial scale.

### Step 1 — Stop the service

```bash
sudo systemctl stop dws@$USER.service
```

### Step 2 — Connect the new scale

Plug in the USB-to-serial adapter. Most adapters appear as `/dev/ttyUSB0`.

### Step 3 — Identify the serial port

```bash
ls -l /dev/ttyUSB* /dev/ttyACM*
# If multiple adapters are present, identify the correct one:
dmesg | tail -20   # shows the most recently connected device
```

### Step 4 — Update configuration if the port changed

If the new scale is on a port other than `/dev/ttyUSB0`:

```bash
nano ~/dws_finalv7/dws_ws/src/dws_ros2/config/dws_params.yaml
# Set:  scale.serial_port: /dev/ttyUSB1   (or the actual port)
```

### Step 5 — Verify baud rate

Most scales default to 9600 baud. Check the scale's manual. If different:

```bash
nano ~/dws_finalv7/dws_ws/src/dws_ros2/config/dws_params.yaml
# Set:  scale.baudrate: 19200   (or the scale's baud rate)
```

### Step 6 — Confirm the user has serial access

```bash
groups $USER | grep dialout
# If not:
sudo usermod -aG dialout $USER
# Then log out and back in
```

### Step 7 — Start and verify

```bash
sudo systemctl start dws@$USER.service
bash deploy/verify_install.sh
```

The `Scale (/dev/ttyUSB0)` line should show `PASS`. Live weight readings appear in HMI → Settings → Scale.

---

## 6. Database corruption

### Symptom
SQLite errors in the journal, the History tab shows no data, or the API returns 500 errors on `/transactions`.

### Step 1 — Diagnose

```bash
sqlite3 ~/dws_finalv7/dws_runtime/database/dws_data.db "PRAGMA integrity_check;"
# Healthy: "ok"
# Corrupt: rows of error messages
```

### Step 2 — Stop the service

```bash
sudo systemctl stop dws@$USER.service
```

### Step 3 — Attempt repair

SQLite can often recover partial data from a corrupt file:

```bash
cd ~/dws_finalv7/dws_runtime/database

# Export whatever is readable
sqlite3 dws_data.db ".recover" > dws_data_recovered.sql 2>/dev/null || \
sqlite3 dws_data.db ".dump" > dws_data_recovered.sql

# Inspect the recovered SQL
wc -l dws_data_recovered.sql
head -30 dws_data_recovered.sql
```

### Step 4a — Restore from backup (recommended)

```bash
# List available backups
ls -lt ~/dws_finalv7/dws_runtime/backups/

# Restore the most recent backup
bash deploy/restore.sh \
  --from ~/dws_finalv7/dws_runtime/backups/<most-recent-timestamp> \
  --force
```

### Step 4b — Fresh start (if no usable backup)

```bash
# Rename corrupt file (do not delete — may still be partially recoverable)
mv ~/dws_finalv7/dws_runtime/database/dws_data.db \
   ~/dws_finalv7/dws_runtime/database/dws_data.db.corrupt.$(date +%Y%m%d)

# Start the service — it will create a fresh database with the correct schema
sudo systemctl start dws@$USER.service
```

### Step 5 — Verify

```bash
sqlite3 ~/dws_finalv7/dws_runtime/database/dws_data.db "PRAGMA integrity_check;"
# Should return: ok
bash deploy/verify_install.sh
```

### Prevention

Backups are created automatically before every update. To add a scheduled backup:

```bash
# Add to crontab (daily at 2am)
(crontab -l 2>/dev/null; echo "0 2 * * * bash ~/dws_finalv7/dws_ws/src/dws_ros2/deploy/backup.sh --quiet --no-logs") | crontab -
```

---

## 7. Lost calibration

### Symptom
Dimension readings are wrong, the HMI shows a calibration warning, or calibration `.npy` files are missing from `dws_runtime/calibration/`.

### Step 1 — Check what is missing

```bash
ls -lh ~/dws_finalv7/dws_runtime/calibration/
```

Required files:
- `stable_empty_depth.npy` — empty-scale reference depth frame
- `noise_map.npy` — per-pixel noise characterisation
- `system_geometry.npy` — camera-to-scale transform

### Step 2 — Restore from backup (if available)

```bash
ls -lt ~/dws_finalv7/dws_runtime/backups/
bash deploy/restore.sh \
  --from ~/dws_finalv7/dws_runtime/backups/<timestamp> \
  --force
```

After restore, verify that the restored calibration is still valid (same camera, same physical mounting):

```bash
bash deploy/verify_install.sh
```

### Step 3 — Run the calibration wizard (new or invalid calibration)

Calibration must be re-run if:
- The camera was replaced
- The camera mounting changed
- Backup calibration is from a different physical unit

```bash
sudo systemctl start dws@$USER.service
# Open http://localhost:8080 → Settings → Calibrate
```

Follow the on-screen steps:
1. Confirm the weighing surface is empty and clear
2. Click "Capture empty reference" — captures `stable_empty_depth.npy`
3. Place a flat reference object and click "Capture noise map"
4. Enter the known dimensions — captures `system_geometry.npy`
5. Verify the calibration result image

### Step 4 — Validate calibration accuracy

Place a box of known dimensions on the scale and scan it. Compare the HMI-reported L × W × H against ground truth. Acceptable tolerance is ±5 mm per axis.

---

## 8. Manual rollback

Use this procedure to manually roll back to any previous commit, independent of `update.sh`.

### Step 1 — Stop service and back up

```bash
sudo systemctl stop dws@$USER.service
bash deploy/backup.sh
```

### Step 2 — Find the target commit

```bash
cd ~/dws_finalv7
git log --oneline -20
```

### Step 3 — Reset to the target commit

```bash
git reset --hard <COMMIT_HASH>
```

### Step 4 — Rebuild

```bash
source /opt/ros/jazzy/setup.bash    # or humble
cd ~/dws_finalv7/dws_ws
colcon build --packages-select dws_ros2
```

### Step 5 — Restore matching database (optional)

If the rollback target used a different database schema, restore a backup from the same era:

```bash
bash deploy/restore.sh \
  --from dws_runtime/backups/<matching-timestamp> \
  --force
```

### Step 6 — Restart and verify

```bash
sudo systemctl start dws@$USER.service
bash deploy/verify_install.sh
```

---

## 9. Full station recovery

Use this procedure to bring a station back from scratch — for example after an OS reinstall, hardware swap, or data centre migration.

### Prerequisites

- A backup created by `deploy/backup.sh` (database, calibration, config)
- The DWS repository at the same commit that created the backup (for schema compatibility)
- The same or equivalent hardware (RealSense camera, serial scale)

---

### Step 1 — Install the OS

Install Ubuntu 22.04 or 24.04 LTS (fresh install, x86_64). Create the operator account with the same username as before.

### Step 2 — Restore the repository

**From git:**

```bash
git clone <your-repo-url> ~/dws_finalv7
cd ~/dws_finalv7
git checkout <commit-hash-matching-backup>
```

**From archive:**

```bash
unzip dws_finalv7_*.zip -d ~/
```

### Step 3 — Run the installer

```bash
sudo bash ~/dws_finalv7/dws_ws/src/dws_ros2/deploy/install.sh
```

Allow the installer to complete fully. Hardware verification warnings for camera and scale are expected at this stage — the service does not need to be running yet.

### Step 4 — Restore runtime data from backup

Copy the backup directory to the new machine, then:

```bash
bash ~/dws_finalv7/dws_ws/src/dws_ros2/deploy/restore.sh \
  --from /path/to/backup/<timestamp> \
  --force
```

This restores:
- SQLite database (transaction history)
- `dws_params.yaml` (station configuration, station ID)
- Calibration files (if the same camera is used)

### Step 5 — Connect hardware

Attach the Intel RealSense camera (USB 3.0) and serial scale (`/dev/ttyUSB0`).

### Step 6 — Verify hardware is detected

```bash
rs-enumerate-devices
ls /dev/ttyUSB*
```

### Step 7 — Calibrate (if camera was replaced or remounted)

If the recovered calibration files do not match the physical installation:

```bash
rm ~/dws_finalv7/dws_runtime/calibration/*.npy
sudo systemctl start dws@$USER.service
# Open http://localhost:8080 → Settings → Calibrate
```

### Step 8 — Start service and run full verification

```bash
sudo systemctl start dws@$USER.service
bash ~/dws_finalv7/dws_ws/src/dws_ros2/deploy/verify_install.sh
```

All checks must pass before the station is returned to service.

### Step 9 — Generate a diagnostic report for records

```bash
bash ~/dws_finalv7/dws_ws/src/dws_ros2/deploy/diagnostics.sh
# Saved to: ~/dws_finalv7/diagnostic_report.txt
```

---

## Appendix — Deploy script reference

| Script | Requires sudo | Purpose |
|--------|--------------|---------|
| `deploy/install.sh` | Yes | Full installation from scratch |
| `deploy/verify_install.sh` | No | Check all subsystems; exit 1 on any FAIL |
| `deploy/diagnostics.sh` | No | Generate `diagnostic_report.txt` |
| `deploy/backup.sh` | No | Timestamped backup to `dws_runtime/backups/` |
| `deploy/restore.sh` | Conditionally* | Restore from backup |
| `deploy/update.sh` | Conditionally* | Pull + rebuild + verify (auto-rollback) |
| `deploy/uninstall.sh` | Conditionally* | Remove service and optionally all data |

\* These scripts use `sudo` only for service management (`systemctl`). If the service is already stopped or `--no-service` is passed, they can run without sudo.

---

## Appendix — Environment variable overrides

All DWS scripts honour these environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `DWS_API_PORT` | `8000` | REST API port |
| `DWS_HMI_STATIC_PORT` | `8080` | HMI static file server port |
| `DWS_WS_PORT` | `8765` | WebSocket port |
| `DWS_MJPEG_PORT` | `8090` | MJPEG video feed port |
| `INSTALL_LOG` | `<repo>/install.log` | Override install log path |
| `DWS_DIAG_OUT` | `<repo>/diagnostic_report.txt` | Override diagnostics output path |
