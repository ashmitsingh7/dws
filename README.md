# DWS Final V7 — Complete System Reference

**Delhivery Dimension Weight Scanner** | Industrial Parcel Measurement System  
Built with Intel RealSense D455 · Python · ROS2 · OpenCV · FastAPI · WebSockets · SQLite

> **Repository:** [github.com/ashmitsingh7/dws_finalv7](https://github.com/ashmitsingh7/dws_finalv7)  
> **Author:** Ashmit Singh — Electronics Engineering (VLSI Design & Technology), VIT  
> **Status:** Active industrial deployment — Delhivery logistics network

---

## Table of Contents

1. [What Is This?](#1-what-is-this)
2. [How It Works — End to End](#2-how-it-works--end-to-end)
3. [System Architecture](#3-system-architecture)
4. [Hardware Requirements](#4-hardware-requirements)
5. [Software Requirements](#5-software-requirements)
6. [Getting the Code](#6-getting-the-code)
7. [Installation — Step by Step](#7-installation--step-by-step)
8. [Configuration](#8-configuration)
9. [Running the System](#9-running-the-system)
10. [The HMI — Operator Interface](#10-the-hmi--operator-interface)
11. [The Full Transaction Pipeline](#11-the-full-transaction-pipeline)
12. [FSM States — Every State Explained](#12-fsm-states--every-state-explained)
13. [Scale Zero Verification Workflow](#13-scale-zero-verification-workflow)
14. [Snapshot & PDF Reports](#14-snapshot--pdf-reports)
15. [REST API Reference](#15-rest-api-reference)
16. [File Structure — Every File Explained](#16-file-structure--every-file-explained)
17. [Runtime Data Layout](#17-runtime-data-layout)
18. [Calibration](#18-calibration)
19. [Health Monitoring](#19-health-monitoring)
20. [Debugging Guide](#20-debugging-guide)
21. [Common Errors and Fixes](#21-common-errors-and-fixes)
22. [Running Without Hardware (Simulation)](#22-running-without-hardware-simulation)
23. [Production Deployment (systemd)](#23-production-deployment-systemd)
24. [Environment Variable Overrides](#24-environment-variable-overrides)
25. [Disk Retention](#25-disk-retention)
26. [Data Schema](#26-data-schema)
27. [Build History](#27-build-history)

---

## 1. What Is This?

This is a complete **industrial Dimension Weight Scanner (DWS)** — the kind of machine you see at airport cargo counters, Delhivery warehouses, and courier sorting hubs. It measures the **length, breadth, height, and weight** of a parcel, links that measurement to a **barcode / AWB (Air Waybill)**, and stores the transaction in a database — all in under 3 seconds.

Commercial equivalents are products like **vMeasure**, **Cubiscan**, and **Mettler Toledo** systems that cost ₹5–20 lakhs per unit. This system replicates that functionality using:

- An **Intel RealSense D455** depth camera (~₹30,000) instead of a laser array
- A **standard serial-port weighing scale** (any CAS/RS232 protocol scale)
- A **USB barcode scanner** (any HID keyboard-mode scanner)
- A commodity **Linux PC or Raspberry Pi 5**

The system runs entirely on-device. No cloud, no subscriptions, no vendor lock-in.

---

## 2. How It Works — End to End

Here is the complete flow, from the operator placing a parcel to a PDF report being generated. Read this section first — everything else in this document is detail on top of this.

```
[Operator places parcel on scale]
           ↓
[Barcode scanner reads AWB label]
           ↓
[Orchestrator receives barcode event]
           ↓
[System waits for scale to stabilize]
  (weight must hold steady for 2 seconds)
           ↓
[Camera captures depth + colour frame]
           ↓
[Vision engine measures L × B × H from 3D point cloud]
           ↓
[Perception engine v7 refines measurement (one-shot)]
           ↓
[Snapshot saved: clean annotated JPEG with L, B, H, Wt overlay]
           ↓
[Transaction written to SQLite database]
           ↓
[HMI updates live — shows dimensions, weight, snapshot]
           ↓
[Operator removes parcel]
           ↓
[WAIT_SCALE_ZERO: system monitors live weight]
  If weight > 0.02 kg → "Press TARE button on scale"
  Once weight < 0.02 kg for 1 second → "Scale Zero Verified"
           ↓
[System returns to IDLE — ready for next parcel]
```

**Nothing in this pipeline is manual.** The operator's only jobs are:
1. Place parcel → scan barcode → wait for green
2. Remove parcel → press TARE if prompted → wait for green

---

## 3. System Architecture

The system runs as **one Python process** containing multiple co-operating nodes. There is no real ROS2 middleware at runtime — the `SimBus` class (`sim_bus.py`) replaces the ROS2 pub/sub layer with in-process message passing, so the system runs without a `ros2 run` environment if needed.

```
┌─────────────────────────────────────────────────────────────────┐
│                         run_dws.py (main process)               │
│                                                                 │
│  ┌──────────────┐    ┌───────────────┐    ┌─────────────────┐  │
│  │  BarcodeNode │    │   ScaleNode   │    │ VisionNodeRS    │  │
│  │  (USB HID    │    │  (Serial RS232│    │ (RealSense D455)│  │
│  │   scanner)   │    │   CAS scale)  │    │                 │  │
│  └──────┬───────┘    └───────┬───────┘    └────────┬────────┘  │
│         │                   │                       │           │
│         │     /dws/barcode  │  /dws/scale           │           │
│         └────────────┬──────┘              ┌────────┘           │
│                      ▼                     │                    │
│             ┌────────────────────┐         │ capture_measurement │
│             │  OrchestratorNode  │◄────────┘                    │
│             │  (FSM controller)  │                              │
│             └────────┬───────────┘                              │
│                      │ /dws/transaction                         │
│         ┌────────────┴────────────┐                             │
│         ▼                         ▼                             │
│  ┌─────────────┐         ┌────────────────┐                     │
│  │DatabaseNode │         │ HmiBridgeNode  │                     │
│  │  (SQLite)   │         │ (WebSocket +   │                     │
│  └─────────────┘         │  HTTP server)  │                     │
│                           └───────┬────────┘                    │
│  ┌──────────────┐                 │                             │
│  │  api_server  │                 │ ws://localhost:8765          │
│  │ (FastAPI REST│                 ▼                             │
│  │  :8000)      │         ┌───────────────┐                     │
│  └──────────────┘         │  index.html   │                     │
│                           │  (operator    │                     │
│  ┌──────────────┐         │   HMI)        │                     │
│  │HealthMonitor │         └───────────────┘                     │
│  │ DiskRetention│                                               │
│  │ SyncWorker   │                                               │
│  └──────────────┘                                               │
└─────────────────────────────────────────────────────────────────┘
```

The three external-facing ports:

| Port | What serves it | What accesses it |
|------|---------------|-----------------|
| `8080` | Python `http.server` serving `hmi/` | Browser → operator HMI |
| `8090` | `aiohttp` (HmiBridgeNode) | Browser → MJPEG video + captures |
| `8000` | FastAPI (api_server.py) | External systems, PDF export, health |
| `8765` | WebSocket (HmiBridgeNode) | Browser ↔ live updates |

---

## 4. Hardware Requirements

### Required

| Component | Spec | Notes |
|-----------|------|-------|
| Intel RealSense D455 | Depth + RGB camera | D415/D435 may work but untested |
| Weighing scale | Serial RS232/USB-serial, CAS protocol | Any scale outputting `+XXXX.XXX\r` format |
| Barcode scanner | USB HID, keyboard-mode | Any scanner that types the AWB into the OS |
| Linux PC | Ubuntu 22.04 or 24.04, x86_64 | Tested on Intel NUC i5, 8GB RAM |
| USB hub | — | Camera + scale + scanner need 3 USB ports |

### Optional / Recommended

| Component | Notes |
|-----------|-------|
| Conveyor belt mount | Camera mounted overhead at 1.5 m clearance |
| Scale platform | Flat, level, vibration-damped |
| UPS | For power fluctuation protection |

### Physical Setup

```
         [RealSense D455 camera]
                  │ (mounted overhead, ~1.5m above scale)
                  │
         ┌────────▼────────┐
         │   Parcel area   │  ← operator places parcel here
         │  (scale pan)    │
         └─────────────────┘
              [Scale]
         
         [Barcode scanner] ← operator scans label
         [PC / NUC]        ← running this software
```

---

## 5. Software Requirements

### Operating System

**Ubuntu 22.04 LTS (Jammy)** or **Ubuntu 24.04 LTS (Noble)** — 64-bit  
Other Linux distros will work with minor path adjustments.

### Python

Python **3.10 or newer** (3.12 recommended).

### ROS2 (Optional)

The system is designed to run with **ROS2 Jazzy** but does not require it for normal operation — the `SimBus` layer replaces the ROS2 middleware. If you want to use real ROS2 topics for integration with other ROS2 nodes, install Jazzy and source `/opt/ros/jazzy/setup.bash`.

### Python Packages

```
numpy>=2.2
scipy>=1.16
opencv-python>=4.12
open3d>=0.19
pillow>=11.0
pyrealsense2
torch>=2.8
torchvision>=0.23
fastapi>=0.116
uvicorn>=0.35
aiohttp>=3.12
websockets>=15.0
requests>=2.32
pydantic>=2.11
reportlab>=4.4
pyserial>=3.5
python-dateutil>=2.9
aiosqlite>=0.21
PyYAML>=6.0
```

---

## 6. Getting the Code

### Clone from GitHub

```bash
git clone https://github.com/ashmitsingh7/dws_finalv7.git
cd dws_finalv7
```

### Directory layout after clone

```
dws_finalv7/
├── dws_ws/
│   ├── src/
│   │   └── dws_ros2/          ← main source package
│   │       ├── dws_ros2/      ← Python modules
│   │       ├── hmi/           ← index.html operator interface
│   │       ├── config/        ← dws_params.yaml
│   │       ├── deploy/        ← run.sh, systemd service file
│   │       ├── pdf_export.py
│   │       ├── api_server.py
│   │       └── README.md
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   └── SETUP.md
├── build/                     ← colcon build output (generated)
└── dws_runtime/               ← all runtime data (generated on first run)
    ├── database/
    ├── captures/
    ├── pointclouds/
    ├── calibration/
    ├── backups/
    ├── exports/
    └── logs/
```

---

## 7. Installation — Step by Step

Copy every command below **exactly** as written. Each step is numbered. Do not skip any.

### Step 1 — Install system dependencies

```bash
sudo apt update && sudo apt upgrade -y

sudo apt install -y \
    python3 python3-pip python3-venv \
    python3-dev build-essential \
    libusb-1.0-0-dev \
    git curl wget \
    libglib2.0-0 libsm6 libxrender1 libxext6 \
    libgl1-mesa-glx
```

### Step 2 — Install Intel RealSense SDK

```bash
# Add Intel repository key
sudo mkdir -p /etc/apt/keyrings
curl -sSf https://librealsense.intel.com/Debian/librealsense.pgp \
  | sudo tee /etc/apt/keyrings/librealsense.pgp > /dev/null

# Add Intel repository
echo "deb [signed-by=/etc/apt/keyrings/librealsense.pgp] \
  https://librealsense.intel.com/Debian/apt-repo \
  $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/librealsense.list

sudo apt update

sudo apt install -y librealsense2-dkms librealsense2-utils librealsense2-dev
```

**Verify the camera is detected:**

```bash
realsense-viewer
```

You should see the D455 in the device list. If not, unplug and replug the USB and try again.

### Step 3 — Clone the repository

```bash
git clone https://github.com/ashmitsingh7/dws_finalv7.git
cd dws_finalv7
```

### Step 4 — Create Python virtual environment

```bash
cd dws_ws
python3 -m venv .venv
source .venv/bin/activate
```

**You must activate the venv every time you open a new terminal:**

```bash
source ~/dws_finalv7/dws_ws/.venv/bin/activate
```

### Step 5 — Install Python dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

This will take several minutes on first install (PyTorch alone is ~2GB).

### Step 6 — Install the package in development mode

```bash
cd src/dws_ros2
pip install -e .
```

### Step 7 — (Optional) Build with colcon for ROS2 integration

Only needed if you want real ROS2 topic bridging:

```bash
# Source ROS2 first
source /opt/ros/jazzy/setup.bash

cd ~/dws_finalv7/dws_ws
colcon build --symlink-install

source install/setup.bash
```

### Step 8 — Verify serial port for scale

```bash
# List serial ports
ls /dev/ttyUSB* /dev/ttyACM*

# Check the scale is producing output
sudo cat /dev/ttyUSB0
```

You should see lines like `+   3.456 kg` scrolling in the terminal. If you see nothing, check:

```bash
# Give your user permission to access serial ports
sudo usermod -a -G dialout $USER
# Log out and back in, then retry
```

### Step 9 — Run the system

```bash
cd ~/dws_finalv7/dws_ws/src/dws_ros2
source ../../.venv/bin/activate

# Development mode (shows logs in terminal)
python3 -m dws_ros2.run_dws
```

Open the HMI in your browser: **http://localhost:8080**

---

## 8. Configuration

All configuration lives in one file: `config/dws_params.yaml`

```yaml
system:
  station_id: STATION_01        # shown on every transaction and PDF report
  simulation_mode: false        # set true to run without hardware (scale stub)

scale:
  serial_port: /dev/ttyUSB0    # change if your scale is on a different port
  baudrate: 9600                # most CAS scales: 9600. Some models: 19200
  publish_rate_hz: 20           # how often the scale is polled (per second)
  buffer_size: 20               # rolling window size for stability detection
  spread_threshold_kg: 0.03     # max allowed range in the window (kg)
  std_dev_threshold_kg: 0.012   # max allowed std deviation (kg)
  required_stable_windows: 8    # how many consecutive stable windows needed
  zero_threshold_kg: 0.05       # below this → scale is considered "empty"
  # Scale zero verification after each transaction:
  zero_verify_threshold_kg: 0.02       # weight must be below this to pass zero check
  zero_verify_stable_duration_s: 1.0   # must hold that weight for this long (seconds)

vision:
  camera_mode: hardware         # only "hardware" is supported
  fps: 30                       # camera frame rate

database:
  file: dws_data.db             # filename inside dws_runtime/database/
  retention_days: 30            # delete captures/pointclouds older than this

api:
  host: 0.0.0.0                 # bind address for REST API
  port: 8000

hmi:
  host: 0.0.0.0
  websocket_port: 8765          # WebSocket for live updates
  http_port: 8090               # MJPEG stream + capture file serving
  static_port: 8080             # HMI HTML file server

health:
  check_interval_seconds: 5     # how often health checks run
  min_free_disk_gb: 2.0         # alert if disk space falls below this

sync:
  enabled: false                # cloud sync (not yet active)
  cloud_url: https://api.dws-cloud.example.com/v1/sync
  poll_interval_seconds: 10
```

### Changing the serial port

If your scale is on `/dev/ttyUSB1` instead:

```bash
# Find it
ls -la /dev/ttyUSB*

# Edit the config
nano ~/dws_finalv7/dws_ws/src/dws_ros2/config/dws_params.yaml
# Change: serial_port: /dev/ttyUSB1
```

### Overriding config with environment variables

Any config value can be overridden without editing the file:

```bash
DWS_SCALE_SERIAL_PORT=/dev/ttyUSB1 python3 -m dws_ros2.run_dws
```

---

## 9. Running the System

### Method 1 — Direct Python (development / debugging)

```bash
cd ~/dws_finalv7/dws_ws/src/dws_ros2
source ../../.venv/bin/activate

python3 -m dws_ros2.run_dws
```

All logs appear in the terminal. `Ctrl+C` to stop.

### Method 2 — Headless (production, no display needed)

```bash
cd ~/dws_finalv7/dws_ws/src/dws_ros2

# Start all three services in background
deploy/run.sh start

# Check they're running
deploy/run.sh status

# View live logs
tail -f ~/.dws/logs/backend.log

# Stop everything
deploy/run.sh stop
```

The three background processes managed by `run.sh`:

| Process | What it does | Log file |
|---------|-------------|----------|
| `backend` | Main DWS pipeline (scale, vision, orchestrator, DB, HMI WS) | `~/.dws/logs/backend.log` |
| `api` | FastAPI REST server on port 8000 | `~/.dws/logs/api.log` |
| `hmi` | Python http.server on port 8080 serving index.html | `~/.dws/logs/hmi.log` |

### Method 3 — systemd (auto-start on boot)

```bash
cd ~/dws_finalv7/dws_ws/src/dws_ros2

# Register the service (only once)
sudo deploy/install_service.sh

# Start now
sudo systemctl start dws@$USER

# Check status
systemctl status dws@$USER

# Follow logs
journalctl -u dws@$USER -f

# Enable auto-start on every boot
sudo systemctl enable dws@$USER

# Stop
sudo systemctl stop dws@$USER
```

### Operations & Recovery Scripts

`dws_ws/src/dws_ros2/deploy/` also includes a set of operator-facing
scripts for backup, recovery, updates, and health checking. These sit
alongside `run.sh`, `stop.sh`, and `install_service.sh` and do not
change how the system is normally started — use them for maintenance,
not day-to-day operation.

| Script | Purpose |
|---|---|
| `deploy/backup.sh [--out <dir>] [--no-logs] [--quiet]` | Creates a timestamped, atomic backup of the SQLite database, runtime config, calibration files, and recent logs under `dws_runtime/backups/` (or `--out`). |
| `deploy/restore.sh --from <backup-dir> [--no-service] [--dry-run] [--force]` | Restores runtime data from a `backup.sh` snapshot. Validates the backup before touching live data and takes an automatic pre-restore safety snapshot first. |
| `deploy/update.sh [--branch <b>] [--tag <t>] [--no-pull] [--force] [--dry-run]` | Safely updates an existing installation: stops services, backs up, pulls/applies the update, rebuilds the workspace, restarts, verifies, and rolls back automatically if verification fails. |
| `deploy/uninstall.sh [--keep-data] [--force] [--dry-run]` | Removes the systemd service and shortcuts. Prompts before deleting `dws_runtime/` unless `--keep-data` is given. |
| `deploy/verify_install.sh [--no-hardware] [--json] [--quiet]` | Checks every subsystem (Python/venv, ROS2, workspace build, hardware, runtime directories, running services) and prints PASS/WARNING/FAIL for each. |
| `deploy/diagnostics.sh [--out <path>] [--no-hardware]` | Collects a full system/software/hardware/DWS-state snapshot into a diagnostic report for troubleshooting — see `deploy/diagnostic_report_example.txt` for sample output. |

All scripts are safe to run as the operator user (no `sudo` required)
except where they touch the systemd service. See `DEPLOYMENT_RECOVERY.md`
at the repo root for full recovery procedures and worked examples.

---

## 10. The HMI — Operator Interface

Open **http://localhost:8080** in any browser (Chrome or Firefox recommended).

### Main Screen Layout

```
┌────────────────────────────────────────────────────────────┐
│ [DELHIVERY DWS]  [DEV MODE] ● Network ● Scale ● Vision     │
│                              [IDLE]          [HH:MM:SS]    │
├──────────────────────┬─────────────────────────────────────┤
│                      │  LAST TRANSACTION                   │
│  [LIVE CAMERA FEED]  │  AWB: 1234567890123                 │
│   (MJPEG stream)     │  L: 39.3 cm                        │
│                      │  B: 29.9 cm                        │
│   [● LIVE]           │  H: 20.2 cm                        │
│                      │  Wt: 3.99 kg                       │
│  [AWB shown here]    │  Vol Wt: 6.72 kg                   │
│                      │  Charge: 6.72 kg                   │
├──────────────────────┤  [Snapshot image]                  │
│ [SCALE ZERO BANNER]  │                                     │
│ (appears post-txn)   ├─────────────────────────────────────┤
│                      │  LIVE ERROR FEED                    │
│                      │  (real-time error log)              │
└──────────────────────┴─────────────────────────────────────┘
```

### Navigation Panels (top icons)

| Panel | What it shows |
|-------|--------------|
| **Dashboard** | Live feed + last transaction (default view) |
| **Calibration** | Spatial calibration status and calibration box management |
| **Measurement** | Live measurement readout |
| **History** | All past transactions, searchable, with snapshot previews |
| **Errors** | Full error log with timestamps and error types |
| **Settings** | Developer mode toggle, station ID, config view |

### Status Dots (top right)

| Dot | Green means | Red means |
|-----|------------|-----------|
| **Network** | API server reachable | `localhost:8000/health` not responding |
| **Scale** | Scale producing readings | Serial disconnected or no readings |
| **Vision** | Camera live | RealSense not streaming |

### System State Badge

The `[IDLE]` badge in the header shows the current FSM state. It updates in real time via WebSocket. All possible values are listed in [Section 12](#12-fsm-states--every-state-explained).

### Scale Zero Banner

After each transaction, a coloured banner appears below the camera feed:

| Banner | Colour | Means |
|--------|--------|-------|
| "Remove parcel from scale" | Amber | Transaction done, parcel still detected |
| "Scale Not Zero — Press TARE button" | Red | Residual weight detected after parcel removed |
| "Waiting for Scale Zero…" | Yellow | Weight close to zero, confirming stability |
| "✓ Scale Zero Verified" | Green | Scale confirmed at zero, next scan enabled |

---

## 11. The Full Transaction Pipeline

This section traces exactly what happens in the code when the operator scans a barcode.

### Phase 1 — Barcode Received

`BarcodeNode` (`barcode_node.py`) runs a background thread reading keyboard input from the USB HID scanner. When a complete barcode string arrives (terminated by Enter), it publishes on `/dws/barcode`.

`OrchestratorNode._barcode_callback()` receives the event. It immediately checks:

- Is `_waiting_scale_zero` True? If yes → **ignore the scan silently** (scale not ready)
- Is the barcode empty? → ERROR
- Is this a calibration box AWB? → route to calibration FSM
- Is this a duplicate of the previous scan within 2 seconds? → ERROR

### Phase 2 — Weight Stabilization

State → `WAITING_FOR_WEIGHT`

The orchestrator polls `ScaleNode.get_measurement()` in a tight loop (100ms sleep). For each reading:

- Weight < `MIN_WEIGHT` (0.05 kg) → no parcel on scale → ERROR
- `scale.stable == True` → stability timer starts
- Stability must hold **continuously** for `REQUIRED_STABLE_TIME` (2 seconds)
- If stability breaks, timer resets
- Total timeout: `STABILITY_TIMEOUT` (10 seconds) → if no stable reading → ERROR

`ScaleNode` itself uses a rolling statistical window:
- Keeps last 20 readings (`buffer_size`)
- Computes mean, standard deviation, and spread (max − min)
- Declares stable when: `spread < 0.03 kg` AND `std_dev < 0.012 kg`, for 8 consecutive windows

State → `STABILIZING` (once stability timer starts)

### Phase 3 — Camera Capture

State → `CAPTURING`

`vision_node_realsense.capture_measurement()` is called with `tx_id` and `weight_kg`.

Inside that function:
- `process_once()` runs the vision pipeline on one frame
- Checks if the result is `valid=True` and `locked=True`
- "Locked" means the measurement has been stable for enough frames

The vision pipeline inside `process_once()`:
1. `RealSenseCamera.get_frames()` → depth array + colour image
2. `MeasurementEngine.process()` (the `realsense_vision` engine):
   - Background subtraction
   - Depth noise filtering
   - Parcel segmentation (contour + depth)
   - OBB (Oriented Bounding Box) fitting
   - Confidence scoring
   - Stability locking (must be consistent across N frames)
3. Overlay drawn on colour frame (bounding box + STABILIZING/LOCKED text)

### Phase 4 — Perception Engine v7 Refinement

After the initial measurement locks, `perception_bridge.refine_measurement()` runs a **second, higher-quality measurement** using the full `perception_engine` v7 pipeline:

- Builds a `Frame` object with calibrated depth
- Runs `MeasurementEngine.measure()` (the v7 engine):
  - Point cloud generation
  - Background plane subtraction
  - Parcel cloud isolation
  - OBB fitting on the 3D cloud
  - Shape type classification (`rectangular` / `irregular`)
  - Confidence scoring
- Saves the parcel point cloud as a `.ply` file to `dws_runtime/pointclouds/`

If the refined measurement succeeds, it **replaces** the initial L/W/H values. If it fails, the initial values are used — the transaction is never blocked by perception engine failure.

### Phase 5 — Snapshot Generation

`SnapshotManager.save()` is called with:
- The annotated colour frame (with bounding box from the vision engine)
- `dimensions` (length_mm, breadth_mm, height_mm)
- `weight_kg` (the confirmed stable weight)

`draw_metadata()` adds a **clean vMeasure-style overlay** in the top-left corner:

```
2026-06-24 11:42:13

L  : 39.3 cm
B  : 29.9 cm
H  : 20.2 cm
Wt : 3.99 kg
```

The image is saved to `dws_runtime/captures/YYYY/MM/DD/parcel_HHMMSS_xxxxxxxx.jpg`

### Phase 6 — Database Write

`DatabaseNode` receives the transaction via `/dws/transaction` and writes it to SQLite. Fields stored:

```
id, awb, tx_id, length, width, height, weight,
vol_weight, charge_weight, confidence, station_id,
operator, created_at, image_url, pointcloud_url,
latency_ms, sync_status, dim_factor
```

### Phase 7 — HMI Update

`HmiBridgeNode` receives the same transaction and broadcasts it over WebSocket to all connected browsers. The HMI updates the last transaction panel instantly.

### Phase 8 — Scale Zero Verification

See [Section 13](#13-scale-zero-verification-workflow).

---

## 12. FSM States — Every State Explained

The system state is always one of these values. It appears in the HMI header badge and in every log line.

| State | What it means | Next state |
|-------|--------------|-----------|
| `BOOTING` | System starting, nodes initialising | `CHECKING_CAMERA` |
| `CHECKING_CAMERA` | Verifying RealSense is connected and streaming | `CHECKING_CALIBRATION` or `ERROR` |
| `CHECKING_CALIBRATION` | Loading `.npy` calibration files | `CHECKING_SCALE` or `CALIBRATION_REQUIRED` |
| `CHECKING_SCALE` | Confirming scale produces readings | `IDLE` or `ERROR` |
| `CALIBRATION_REQUIRED` | No valid calibration — only calibration box scans accepted | `IDLE` (after calibration) |
| `IDLE` | Ready for next barcode scan | `WAITING_FOR_WEIGHT` |
| `WAITING_FOR_WEIGHT` | Barcode received, polling scale for stability | `STABILIZING` or `ERROR` |
| `STABILIZING` | Scale stability timer running | `CAPTURING` or `ERROR` |
| `CAPTURING` | Camera capturing measurement | `PROCESSING` or `ERROR` |
| `PROCESSING` | Perception engine running refinement | `COMPLETE` |
| `COMPLETE` | Transaction successfully saved | `REMOVE_PARCEL` |
| `REMOVE_PARCEL` | Prompting operator to remove parcel | `WAIT_SCALE_ZERO` or `SCALE_NOT_ZERO` |
| `WAIT_SCALE_ZERO` | Weight near zero, confirming stability | `SCALE_ZERO_VERIFIED` |
| `SCALE_NOT_ZERO` | Residual weight — operator must press TARE | `WAIT_SCALE_ZERO` |
| `SCALE_ZERO_VERIFIED` | Zero confirmed, brief display | `IDLE` |
| `ERROR` | Transaction failed | `IDLE` or `CALIBRATION_REQUIRED` |

---

## 13. Scale Zero Verification Workflow

After every successful transaction, the system enters a mandatory zero-check cycle. **The software never sends a tare command** — only the operator pressing the physical TARE button on the scale can reset it.

```
Transaction complete
       ↓
REMOVE_PARCEL
  "Remove parcel from scale"
  [monitoring live weight continuously]
       ↓
  weight > 0.02 kg?
       │
      YES → SCALE_NOT_ZERO
             "Scale Not Zero — Please remove the parcel
              and press the TARE button on the weighing scale"
             [keep monitoring]
       │
      NO (weight ≤ 0.02 kg)
       ↓
WAIT_SCALE_ZERO
  "Waiting for Scale Zero…"
  [must hold ≤ 0.02 kg continuously for 1 second]
       ↓
  held for 1 second?
       │
      YES → SCALE_ZERO_VERIFIED
             "✓ Scale Zero Verified — Ready for next transaction"
             [1 second display]
             → IDLE
       │
      NO → weight drifted above threshold → back to SCALE_NOT_ZERO
```

### Configuration

```yaml
scale:
  zero_verify_threshold_kg: 0.02       # abs(weight) must be below this
  zero_verify_stable_duration_s: 1.0   # must hold for this many seconds
```

### Barcode Lock

During the entire `WAIT_SCALE_ZERO` cycle:
- All barcode scans are silently ignored
- The orchestrator sets `_waiting_scale_zero = True` before entering the cycle
- It is cleared in a `finally` block so it **always** clears, even if something crashes

---

## 14. Snapshot & PDF Reports

### Snapshot Image

Every successful transaction saves a JPEG to:

```
dws_runtime/captures/YYYY/MM/DD/parcel_HHMMSS_xxxxxxxx.jpg
```

The image contains:
- Full-resolution colour frame from the RealSense
- Green bounding box around the detected parcel
- Clean measurement overlay (top-left corner):
  ```
  2026-06-24 11:42:13

  L  : 39.3 cm
  B  : 29.9 cm
  H  : 20.2 cm
  Wt : 3.99 kg
  ```

The overlay uses a semi-transparent black background (55% opacity) so the parcel is still visible behind it.

### Viewing a Snapshot

Via HTTP: `http://localhost:8090/captures/YYYY/MM/DD/parcel_HHMMSS_xxxxxxxx.jpg`  
Via HMI: click any transaction in the History panel.

### PDF Report — Single Transaction

```bash
curl "http://localhost:8000/export/pdf/transaction/42" \
  -o transaction_42.pdf
```

Or in the HMI: History → click transaction row → download PDF button.

The PDF contains:
- Header: **DELHIVERY DWS REPORT** + generation timestamp
- Transaction information: Transaction ID, AWB, Timestamp, Station ID, Operator ID
- Measurements table: Length, Breadth, Height, Weight
- Parcel snapshot (the full annotated JPEG, scaled to fit)
- Point cloud reference: filename of the `.ply` file

### PDF Report — Batch Export

```bash
# Export last 7 days
curl "http://localhost:8000/export/pdf/summary?days=7" \
  -o summary_7days.pdf

# Export by date range
curl "http://localhost:8000/export/pdf/summary?from=2026-06-01&to=2026-06-24" \
  -o summary_june.pdf
```

---

## 15. REST API Reference

Base URL: `http://localhost:8000`

### Health

```
GET /health
```

Response:
```json
{
  "status": "ok",
  "camera": "ok",
  "scale": "ok",
  "database": "ok",
  "disk": "ok",
  "calibration": "ok",
  "free_disk_gb": 47.3,
  "stale": false
}
```

### Transactions

```
GET /transactions                          list all (paginated)
GET /transactions?page=1&limit=50         paginated
GET /transactions?awb=1234567890          filter by AWB
GET /transactions?from=2026-06-01         filter by date
GET /transaction/{id}                     single transaction by ID
```

### Export

```
GET /export/csv                            all transactions as CSV
GET /export/csv?days=7                     last 7 days
GET /export/pdf/transaction/{id}          single PDF
GET /export/pdf/summary                    all transactions PDF
GET /export/pdf/summary?days=30           last 30 days
```

### Captures & Point Clouds

```
GET /captures/{path}                      serve snapshot JPEG
GET /pointclouds/{filename}              serve .ply point cloud
```

### System

```
GET /system                               current system state + metrics
POST /dws/update_system_state            update developer_mode, station_id
GET /dws/get_system_state                read current system_state record
```

---

## 16. File Structure — Every File Explained

```
dws_ws/src/dws_ros2/
│
├── dws_ros2/                          ← Python package (importable as dws_ros2)
│   │
│   ├── run_dws.py                     ← ENTRY POINT. Starts all nodes in one process.
│   │
│   ├── orchestrator_node.py           ← FSM controller. The brain. Reads barcodes,
│   │                                    waits for scale stability, triggers camera,
│   │                                    assembles transactions, manages WAIT_SCALE_ZERO.
│   │
│   ├── vision_node_realsense.py       ← RealSense camera node. Live streaming,
│   │                                    measurement locking, snapshot generation,
│   │                                    calls perception_bridge for refinement.
│   │
│   ├── scale_node.py                  ← Serial scale node. Rolling statistical
│   │                                    stability detection. CAS protocol parser.
│   │
│   ├── barcode_node.py                ← USB HID barcode reader. Publishes AWBs.
│   │
│   ├── database_node.py               ← SQLite persistence. Transaction saving,
│   │                                    history queries, error logging.
│   │
│   ├── hmi_bridge_node.py             ← WebSocket server (port 8765) + aiohttp
│   │                                    HTTP server (port 8090). Bridges backend
│   │                                    events to browser HMI.
│   │
│   ├── perception_bridge.py           ← Thin adapter between vision_node_realsense
│   │                                    and perception_engine v7. Runs one-shot
│   │                                    refinement. Saves point clouds.
│   │
│   ├── config_loader.py               ← Loads dws_params.yaml, applies env var
│   │                                    overrides, provides get_config().
│   │
│   ├── messages.py                    ← Dataclass definitions: ScaleMsg, BarcodeMsg,
│   │                                    TransactionMsg, DimensionsMsg, etc.
│   │
│   ├── sim_bus.py                     ← In-process pub/sub bus replacing ROS2
│   │                                    middleware. publish(), subscribe(), call_service().
│   │
│   ├── paths.py                       ← Single source of truth for all filesystem
│   │                                    paths. Resolves dws_runtime/ location.
│   │
│   ├── health_monitor.py              ← Background thread running health checks
│   │                                    every N seconds. Camera, scale, DB, disk,
│   │                                    calibration checks.
│   │
│   ├── disk_retention.py              ← Background thread pruning captures/
│   │                                    and pointclouds/ older than retention_days.
│   │
│   ├── sync_worker_node.py            ← Cloud sync worker (currently disabled).
│   │
│   ├── vision_node.py                 ← Legacy simulation vision node (not used
│   │                                    in hardware mode).
│   │
│   └── realsense_vision/              ← realsense_vision sub-package
│       ├── visualization.py           ← Live frame overlay renderer (bounding box,
│       │                                STABILIZING/LOCKED text, dimension panel
│       │                                for live view only).
│       ├── snapshot_manager.py        ← Saves annotated JPEG snapshots. Draws
│       │                                clean vMeasure-style overlay with L/B/H/Wt.
│       ├── measurement_engine.py      ← Core vision measurement (contour, OBB,
│       │                                depth integration). Used for live locking.
│       ├── calibration_manager.py     ← Loads/saves calibration .npy files.
│       ├── auto_calibration_v2.py     ← Automated calibration using calibration box.
│       ├── human_detector.py          ← YOLOv8-based human presence detection.
│       │                                Blocks measurement if human hand detected.
│       ├── realsense_camera.py        ← pyrealsense2 wrapper. Frame acquisition,
│       │                                intrinsics, depth scale.
│       └── calibration/               ← Calibration data (written by calibration FSM)
│           ├── calibration_result.jpg    visual reference image
│           ├── system_geometry.npy       camera geometry parameters
│           ├── noise_map.npy             per-pixel depth noise profile
│           └── stable_empty_depth.npy    empty scale baseline depth map
│
├── perception_engine/                 ← Perception engine v7 (higher quality,
│   │                                    used once per transaction for refinement)
│   ├── measurement_engine.py          ← Top-level: Frame → MeasurementResult
│   ├── segmentation/parcel_segmenter.py  ← Isolates parcel point cloud
│   ├── measurement/obb.py             ← 3D oriented bounding box
│   ├── measurement/estimator.py       ← L/W/H from OBB
│   ├── measurement/confidence.py      ← Confidence scoring
│   ├── geometry/pointcloud.py         ← NumPy ↔ Open3D conversion
│   ├── geometry/plane.py              ← Ground plane detection/subtraction
│   ├── background/subtractor.py       ← Background subtraction
│   ├── background/noise_filter.py     ← Depth noise filtering
│   ├── calibration/loader.py          ← Calibration file loader
│   └── capture/                       ← Frame and Intrinsics dataclasses
│
├── hmi/
│   └── index.html                     ← Single-page operator HMI. All CSS, JS,
│                                        HTML in one file. WebSocket client,
│                                        MJPEG consumer, transaction display,
│                                        calibration UI, error log, history,
│                                        scale-zero banner.
│
├── config/
│   └── dws_params.yaml                ← All configuration. See Section 8.
│
├── deploy/
│   ├── run.sh                         ← Headless process manager. start/stop/
│   │                                    status/restart commands.
│   ├── dws.service                    ← systemd unit file for auto-start.
│   ├── install_service.sh             ← Registers and enables systemd service.
│   └── stop.sh                        ← Emergency stop script.
│
├── pdf_export.py                      ← ReportLab PDF generation. Single-transaction
│                                        and batch summary reports.
│
└── api_server.py                      ← FastAPI REST server. Transaction queries,
                                         CSV/PDF export, capture serving, health.
```

---

## 17. Runtime Data Layout

All data generated at runtime lives in `dws_runtime/` (beside `dws_ws/` in the repo root):

```
dws_runtime/
├── database/
│   └── dws_data.db          ← SQLite database (all transactions, errors, system state)
├── captures/
│   └── YYYY/
│       └── MM/
│           └── DD/
│               └── parcel_HHMMSS_xxxxxxxx.jpg    ← annotated snapshots
├── pointclouds/
│   └── <tx_uuid>.ply        ← Open3D point clouds from perception engine v7
├── calibration/             ← calibration .npy files (also in source tree)
├── backups/                 ← (future: database backup copies)
├── exports/                 ← (future: exported files)
└── logs/                    ← (used by run.sh backend)
```

**To back up all transaction data:**

```bash
# Back up database
cp ~/dws_finalv7/dws_runtime/database/dws_data.db ~/backup_$(date +%Y%m%d).db

# Back up everything
tar -czf ~/dws_backup_$(date +%Y%m%d).tar.gz ~/dws_finalv7/dws_runtime/
```

---

## 18. Calibration

Calibration teaches the system:
1. Where the scale platform is in the camera's field of view
2. The empty baseline depth map (what the scene looks like with nothing on the scale)
3. The per-pixel noise profile

### When is calibration needed?

- First time the camera is mounted
- Any time the camera is moved or rotated
- After 100 transactions (the system warns at 90, locks at 100 in production mode)

### How to calibrate

1. Place the **calibration box** on the scale
2. Scan its barcode (it's a special AWB recognised by the system)
3. The system runs `auto_calibration_v2.py` automatically
4. Wait for `CALIBRATION_PASS` state in the HMI
5. The `.npy` files are written to `realsense_vision/calibration/`

### Developer Mode

In Developer Mode, calibration is tracked but **never enforced** — you can scan and measure without calibrating. This is the default for development.

To check or change:

```bash
curl http://localhost:8000/dws/get_system_state
# Look for "developer_mode": true/false

# Enable developer mode
curl -X POST http://localhost:8000/dws/update_system_state \
  -H "Content-Type: application/json" \
  -d '{"developer_mode": true}'
```

---

## 19. Health Monitoring

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "ok",
  "camera": "ok",
  "scale": "ok",
  "database": "ok",
  "disk": "ok",
  "calibration": "ok",
  "free_disk_gb": 47.3,
  "stale": false
}
```

Each field is a **real check**, not cached:

- `camera` — tried to read a live frame from the RealSense in the last 5 seconds
- `scale` — scale produced a reading within the last 2 seconds
- `database` — `SELECT 1` executed successfully
- `disk` — `shutil.disk_usage()` against the captures volume > `min_free_disk_gb`
- `calibration` — all three `.npy` files exist and are loadable

If anything is wrong:

```json
{
  "status": "degraded",
  "scale": "error",
  "scale_reason": "SCALE_SERIAL_DISCONNECTED"
}
```

---

## 20. Debugging Guide

### The system won't start

**Symptom:** `python3 -m dws_ros2.run_dws` exits immediately or shows an import error.

```bash
# Check Python version
python3 --version
# Must be 3.10+

# Check all packages installed
pip list | grep -E "numpy|opencv|pyrealsense|torch|fastapi|aiohttp|websockets"

# Re-install if missing
pip install -r requirements.txt
```

### Camera not detected

```bash
# Check USB connection
lsusb | grep Intel

# Test the camera directly
realsense-viewer

# Check driver
dmesg | tail -30 | grep -i realsense

# Try a different USB port (must be USB 3.0)
```

**Expected output in logs:**

```
[vision_node_realsense][INFO] RealSense D455 connected: 848x480 @ 30fps
```

### Scale not reading

```bash
# Find the port
ls /dev/ttyUSB* /dev/ttyACM*

# Test raw serial output
sudo stty -F /dev/ttyUSB0 9600 cs8 -cstopb -parenb raw
sudo cat /dev/ttyUSB0

# You should see weight strings like:
#   +   3.456 kg

# If nothing: check the baud rate (try 19200 or 4800)
# If permission denied:
sudo usermod -a -G dialout $USER
# Log out and back in
```

**Expected output in logs:**

```
[scale_node][INFO] Connected:/dev/ttyUSB0
[scale_node][INFO] [STABLE] 3.456 kg | spread=0.00100 | std=0.00041
```

### HMI shows "Connecting..." forever

```bash
# Check WebSocket server is running
ss -tlnp | grep 8765

# Check HTTP servers
ss -tlnp | grep -E "8080|8090"

# Check for errors in backend log
tail -50 ~/.dws/logs/backend.log
# or if running directly:
# watch the terminal where you ran python3 -m dws_ros2.run_dws
```

### Measurements are wrong

```bash
# Check calibration files exist
ls -la ~/dws_finalv7/dws_ws/src/dws_ros2/dws_ros2/realsense_vision/calibration/

# Expected:
# calibration_result.jpg
# system_geometry.npy
# noise_map.npy
# stable_empty_depth.npy

# If missing → run calibration (see Section 18)

# Check the camera height
# Camera must be mounted at the height specified during calibration
# Default: 1.5m above the scale platform

# Check the workspace polygon in the HMI
# The orange polygon should outline the scale platform area
```

### Barcode scans are ignored

```bash
# Check the system isn't in WAIT_SCALE_ZERO
# Look at the HMI header badge

# If stuck in WAIT_SCALE_ZERO:
# 1. Remove any weight from the scale
# 2. Press TARE on the physical scale
# 3. Wait for "Scale Zero Verified" in the HMI

# In logs, look for:
grep "SCALE_ZERO\|WAIT_SCALE\|barcode ignored" ~/.dws/logs/backend.log
```

### Transaction not saving to database

```bash
# Check database exists
ls -la ~/dws_finalv7/dws_runtime/database/

# Query it directly
sqlite3 ~/dws_finalv7/dws_runtime/database/dws_data.db \
  "SELECT id, awb, created_at, length, weight FROM transactions ORDER BY id DESC LIMIT 5;"

# Check for DB errors in logs
grep "DB\|database\|sqlite" ~/.dws/logs/backend.log | tail -20
```

### View all logs live

```bash
# If running via run.sh:
tail -f ~/.dws/logs/backend.log | grep -v "STABLE\|poll"

# If running directly in terminal:
# All logs appear in the terminal with colour coding
# Filter by component:
# [orchestrator_node] [scale_node] [vision_node_realsense] etc.
```

### Enable verbose debug logging

```bash
DWS_LOG_LEVEL=DEBUG python3 -m dws_ros2.run_dws
```

### Check a specific transaction in the database

```bash
sqlite3 ~/dws_finalv7/dws_runtime/database/dws_data.db << 'SQL'
.mode column
.headers on
SELECT
  id,
  awb,
  length,
  width,
  height,
  weight,
  ROUND(charge_weight, 2) as charge_wt,
  created_at,
  image_url
FROM transactions
ORDER BY id DESC
LIMIT 10;
SQL
```

### Restart cleanly

```bash
# If running in terminal: Ctrl+C
# Then restart:
python3 -m dws_ros2.run_dws

# If running via run.sh:
deploy/run.sh restart

# If running as systemd service:
sudo systemctl restart dws@$USER
```

---

## 21. Common Errors and Fixes

### `ModuleNotFoundError: No module named 'pyrealsense2'`

```bash
pip install pyrealsense2
# If that fails:
pip install pyrealsense2 --extra-index-url https://pypi.org/simple
```

### `ImportError: libGL.so.1: cannot open shared object file`

```bash
sudo apt install -y libgl1-mesa-glx libglib2.0-0
```

### `serial.SerialException: [Errno 13] Permission denied: '/dev/ttyUSB0'`

```bash
sudo usermod -a -G dialout $USER
# Log out completely, log back in, try again
```

### `RuntimeError: No RealSense devices connected`

```bash
# Unplug the camera
# Wait 5 seconds
# Plug it back in to a USB 3.0 port (blue port)
# Try again
realsense-viewer  # verify camera shows up here first
```

### HMI shows `CALIBRATION_REQUIRED` and won't scan

Either:
1. Enable Developer Mode (see Section 18) to bypass calibration
2. Run a proper calibration using the calibration box

### `Address already in use` on port 8000 / 8080 / 8090

```bash
# Find and kill whatever is using the port
sudo ss -tlnp | grep 8000
# Note the PID, then:
kill -9 <PID>

# Or stop any existing DWS instance:
deploy/run.sh stop
```

### Scale reads `0.000 kg` always

The scale zero threshold (`zero_threshold_kg: 0.05`) treats readings below 0.05 kg as "empty". If your scale is outputting tiny fluctuating values when empty, increase this threshold:

```yaml
scale:
  zero_threshold_kg: 0.1
```

### Snapshot overlay shows wrong dimensions

The snapshot is saved at the moment of camera lock. The dimensions on the overlay may differ slightly from the final database values if the perception engine v7 refinement improves them. The database always stores the best (most refined) values.

---

## 22. Running Without Hardware (Simulation)

If you don't have the camera or scale, you can still run and test the system.

### Scale simulation

Edit `config/dws_params.yaml`:

```yaml
system:
  simulation_mode: true
```

In simulation mode, the `ScaleNode` generates random weight readings around 5.0 kg instead of reading serial. The camera still needs to be present (or see below).

### Camera simulation

The system does not currently have a software camera stub. To test without a camera:

```bash
# Use a pre-recorded bag file (if available)
# Or modify vision_node_realsense.py to load from disk:
# self._stub_image = cv2.imread("test_parcel.jpg")
```

### Full stub mode (no hardware at all)

```bash
# Set simulation_mode: true in dws_params.yaml
# Then run — the scale will simulate and barcodes can be
# typed directly into the terminal running barcode_node
```

To inject a fake barcode scan:

```bash
curl -X POST http://localhost:8000/inject/barcode \
  -H "Content-Type: application/json" \
  -d '{"awb": "1234567890123"}'
```

*(If this endpoint doesn't exist, publish directly via the API server or by modifying the BarcodeNode to read from stdin.)*

---

## 23. Production Deployment (systemd)

For a permanent installation where the DWS should start automatically when the PC boots:

### Step 1 — Install the service

```bash
cd ~/dws_finalv7/dws_ws/src/dws_ros2
sudo deploy/install_service.sh
```

### Step 2 — Start it

```bash
sudo systemctl start dws@$USER
```

### Step 3 — Check it's running

```bash
systemctl status dws@$USER
```

Expected output:
```
● dws@yourusername.service - DWS Industrial Scanner
     Loaded: loaded (/etc/systemd/system/dws@.service; enabled)
     Active: active (running) since ...
```

### Step 4 — Enable auto-start on boot

```bash
sudo systemctl enable dws@$USER
```

### Step 5 — Follow logs

```bash
journalctl -u dws@$USER -f
```

### Step 6 — Stop

```bash
sudo systemctl stop dws@$USER
```

### Useful systemctl commands

```bash
# Restart after config change
sudo systemctl restart dws@$USER

# Disable auto-start
sudo systemctl disable dws@$USER

# Check if enabled
systemctl is-enabled dws@$USER

# See last 100 log lines
journalctl -u dws@$USER -n 100 --no-pager
```

---

## 24. Environment Variable Overrides

Every config value in `dws_params.yaml` can be overridden with an environment variable. This is useful for containerised deployments or when you want to change one value without editing the file.

| Environment variable | Config equivalent | Example |
|---------------------|-------------------|---------|
| `DWS_RUNTIME_ROOT` | (runtime data root) | `/data/dws_runtime` |
| `DWS_CAPTURE_DIR` | — | `/mnt/nas/captures` |
| `DWS_POINTCLOUD_DIR` | — | `/mnt/nas/pointclouds` |
| `DWS_CALIBRATION_DIR` | — | `/etc/dws/calibration` |
| `DWS_DB_PATH` | `database.file` | `/data/dws.db` |
| `DWS_LOG_DIR` | — | `/var/log/dws` |
| `DWS_HMI_DIR` | — | `/var/www/dws-hmi` |
| `DWS_API_PORT` | `api.port` | `9000` |
| `DWS_HMI_STATIC_PORT` | `hmi.static_port` | `80` |
| `DWS_RUN_DIR` | — | `/run/dws` |

Example — run the system with data stored on a NAS:

```bash
DWS_RUNTIME_ROOT=/mnt/nas/dws_runtime \
DWS_DB_PATH=/mnt/nas/dws_data.db \
python3 -m dws_ros2.run_dws
```

---

## 25. Disk Retention

The system automatically deletes old captures and point clouds to prevent the disk from filling up.

**Policy:** Files in `dws_runtime/captures/` and `dws_runtime/pointclouds/` older than `database.retention_days` (default: 30 days) are deleted once per hour.

**The database is never pruned** — transaction records stay forever, even after the corresponding images are deleted. An attempt to serve a deleted image will 404.

To keep everything forever:

```yaml
database:
  retention_days: 0
```

To check what's taking disk space:

```bash
du -sh ~/dws_finalv7/dws_runtime/captures/
du -sh ~/dws_finalv7/dws_runtime/pointclouds/

# Breakdown by month
du -sh ~/dws_finalv7/dws_runtime/captures/2026/*/
```

---

## 26. Data Schema

The SQLite database (`dws_data.db`) has these main tables:

### `transactions`

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment row ID |
| `awb` | TEXT | Barcode / Air Waybill number |
| `tx_id` | TEXT | UUID for this transaction |
| `length` | REAL | Length in mm |
| `width` | REAL | Breadth in mm |
| `height` | REAL | Height in mm |
| `weight` | REAL | Actual weight in kg |
| `vol_weight` | REAL | Volumetric weight = L×B×H / DIM_FACTOR |
| `charge_weight` | REAL | max(actual, vol_weight) |
| `confidence` | REAL | Measurement confidence 0.0–1.0 |
| `station_id` | TEXT | Station identifier from config |
| `operator` | TEXT | Operator ID (future feature) |
| `created_at` | TEXT | ISO timestamp |
| `image_url` | TEXT | Absolute path to snapshot JPEG |
| `pointcloud_url` | TEXT | Absolute path to `.ply` file |
| `latency_ms` | REAL | Barcode-to-save latency in milliseconds |
| `sync_status` | INTEGER | 0=unsynced, 1=synced |
| `dim_factor` | REAL | Dimensional weight divisor (5000) |

### `errors`

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | — |
| `error_type` | TEXT | e.g. `WEIGHT_UNSTABLE_TIMEOUT` |
| `awb` | TEXT | AWB that triggered the error |
| `message` | TEXT | Human-readable description |
| `severity` | TEXT | `HIGH` or `MEDIUM` |
| `component` | TEXT | `hardware` or `processing` |
| `created_at` | TEXT | ISO timestamp |
| `station_id` | TEXT | — |
| `tx_id` | TEXT | — |

### `system_state`

Single-row table persisting:
- `developer_mode` (0/1)
- `calibration_required` (0/1)
- `calibration_locked` (0/1)
- `shipments_since_calibration`

### Useful SQL queries

```bash
# Connect to the database
sqlite3 ~/dws_finalv7/dws_runtime/database/dws_data.db

# Count transactions today
SELECT COUNT(*) FROM transactions WHERE created_at LIKE '2026-06-24%';

# Total weight processed today
SELECT ROUND(SUM(weight), 2) || ' kg' FROM transactions WHERE created_at LIKE '2026-06-24%';

# Most recent 10 transactions
SELECT id, awb, ROUND(length,1), ROUND(width,1), ROUND(height,1), ROUND(weight,3), created_at
FROM transactions ORDER BY id DESC LIMIT 10;

# Average measurement confidence
SELECT ROUND(AVG(confidence)*100, 1) || '%' FROM transactions;

# Error count by type
SELECT error_type, COUNT(*) as count FROM errors GROUP BY error_type ORDER BY count DESC;

# Exit
.quit
```

---

## 27. Build History

| Build | Key additions |
|-------|--------------|
| **Build 001** | Headless startup (`run.sh`), systemd service, no display required |
| **Build 002** | Central YAML config, honest `/health` endpoint, disk retention |
| **Build 003** | Perception engine v7 integration, point cloud capture, Open3D |
| **Build 004** | Professional PDF export (ReportLab), batch summary reports |
| **Build 005** | Developer Mode (default ON), calibration tracking without enforcement |
| **Build 006** | All runtime data unified under `dws_runtime/`, path resolution refactor |
| **Build 007 (current)** | Clean vMeasure-style snapshot overlay (L/B/H/Wt only, semi-transparent, in cm), weight passed through to snapshot, WAIT_SCALE_ZERO FSM state with operator TARE guidance |

---

## Contact

**Ashmit Singh**  
Electronics Engineering (VLSI Design & Technology) — Vellore Institute of Technology

GitHub: [github.com/ashmitsingh7](https://github.com/ashmitsingh7)  
LinkedIn: [linkedin.com/in/ashmitsingh7](https://www.linkedin.com/in/ashmitsingh7)

---

*This system is under active development for Delhivery logistics infrastructure. Some advanced features (multi-camera fusion, conveyor integration, cloud sync) are in progress.*
