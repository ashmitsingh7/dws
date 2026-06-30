# Industrial DWS System — RealSense + ROS2

An industrial-grade Dimension Weight Scanning (DWS) system built using:

- Intel RealSense D455
- ROS2
- Python
- Real-time MJPEG streaming
- Web-based HMI
- Barcode-triggered transaction pipeline
- Automated dimension extraction
- Weight integration
- SQLite transaction logging
- Industrial FSM orchestration

---

# Overview

This project is a fully modular industrial parcel dimensioning pipeline designed for logistics, warehouse automation, and shipping environments.

The system performs:

- Real-time parcel detection
- Parcel dimension extraction
- Weight synchronization
- Barcode-linked transaction creation
- Live operator visualization
- Transaction image archival
- Database logging
- WebSocket-based HMI synchronization

The architecture was redesigned from an earlier Cognex-based pipeline into a RealSense D455 based intelligent vision system.

---

# System Architecture

## Core Components

### 1. Vision Node (`vision_node_realsense.py`)

Handles:

- RealSense D455 interface
- Depth acquisition
- RGB acquisition
- Measurement engine
- Human detection
- Annotated visualization
- MJPEG live streaming
- Snapshot generation
- Transaction frame freezing

---

### 2. Orchestrator (`orchestrator_node.py`)

Finite State Machine controller.

Responsible for:

- System state transitions
- Stabilization logic
- Barcode synchronization
- Capture sequencing
- Error handling
- Transaction assembly
- Database sync coordination

FSM states include:

- BOOTING
- CHECKING_CAMERA
- CHECKING_CALIBRATION
- CHECKING_SCALE
- IDLE
- DETECTED
- STABILIZING
- CAPTURING
- PROCESSING
- SYNCING
- COMPLETE
- ERROR

---

### 3. Scale Node (`scale_node.py`)

Handles:

- Serial communication
- Weight stabilization
- Continuous polling
- Scale health monitoring

---

### 4. HMI Bridge (`hmi_bridge_node.py`)

Responsible for:

- WebSocket broadcasting
- Live transaction sync
- MJPEG feed coordination
- Frontend data publishing

---

### 5. HMI Frontend (`hmi/index.html`)

Industrial operator interface providing:

- Live camera feed
- Real-time measurements
- Throughput stats
- Calibration status
- Error feed
- Transaction logs
- Historical transaction lookup

---

### 6. Database Node (`database_node.py`)

Handles:

- SQLite transaction persistence
- History retrieval
- Sync acknowledgements

---

# Features

## Real-Time MJPEG Video Streaming

- Live RealSense stream
- Automatic reconnect
- Stream freeze on successful transaction
- Annotated overlays

---

## Intelligent Parcel Measurement

Measures:

- Length
- Width
- Height
- Confidence
- Object count

Uses:

- Depth segmentation
- Contour extraction
- Stability verification
- Human rejection filtering

---

## Industrial FSM Logic

The orchestrator uses deterministic industrial FSM sequencing for reliable logistics workflows.

This prevents:

- Duplicate captures
- Motion instability
- Premature scans
- False positives

---

## Barcode-Driven Acquisition

Transactions are synchronized to barcode events.

Pipeline:

Barcode → Stable Weight → Stable Object → Capture → DB Sync → HMI Update

---

## Snapshot Archival

Every successful transaction stores:

- Annotated parcel image
- Timestamp
- Dimensions
- Weight
- Transaction metadata

---

# Tech Stack

| Layer | Technology |
|---|---|
| Vision | Intel RealSense D455 |
| Backend | Python |
| Middleware | ROS2 |
| HMI | HTML/CSS/JavaScript |
| Streaming | MJPEG |
| Database | SQLite |
| Messaging | WebSockets |
| Detection | OpenCV + YOLO |

---

# Folder Structure

```bash
dws_ros2/
│
├── dws_ros2/
│   ├── vision_node_realsense.py
│   ├── orchestrator_node.py
│   ├── scale_node.py
│   ├── database_node.py
│   ├── hmi_bridge_node.py
│   ├── barcode_node.py
│   ├── api_server.py
│   └── ...
│
├── hmi/
│   └── index.html
│
├── launch/
├── config/
├── captures/
├── calibration/
└── README.md
```

---

# Running The System

## Activate Environment

```bash
source ~/realsense_env/bin/activate
```

---

## Launch DWS (interactive / development)

```bash
cd ~/dws_ws/src/dws_ros2
./start_dws.sh
```

Opens three `gnome-terminal` windows (backend, REST API, HMI server).
Requires a desktop session — will fail over SSH or on a headless boot.

## Launch DWS (headless / production)

```bash
cd ~/dws_ws/src/dws_ros2
deploy/run.sh start      # build, hardware checks, start all 3 services detached
deploy/run.sh status     # check what's running
deploy/run.sh stop       # graceful stop (SIGTERM, falls back to SIGKILL after 10s)
deploy/run.sh restart
```

No display required. Logs go to `~/.dws/logs/{backend,api,hmi}.log`,
PID files to `~/.dws/run/`.

## Auto-start on boot (systemd)

```bash
sudo deploy/install_service.sh        # registers + enables the service
sudo systemctl start dws@$USER        # start now
systemctl status dws@$USER            # check
journalctl -u dws@$USER -f            # follow logs
```

The unit (`deploy/dws.service`) auto-restarts on crash (`Restart=on-failure`,
5s backoff) and auto-starts after boot once enabled. Stopping the service
sends SIGTERM to the backend, which already has a graceful shutdown path
(see `run_dws.py`) that stops every node and closes the database cleanly
before the process exits.

---

# Configuration

All runtime-tunable settings live in one file — `config/dws_params.yaml` —
read by `dws_ros2/config_loader.py`. No code edits are required to change
any of these:

```yaml
scale:
  serial_port: /dev/ttyUSB0
  baudrate: 9600
  buffer_size: 20               # samples in the stability window
  spread_threshold_kg: 0.03
  std_dev_threshold_kg: 0.012
  required_stable_windows: 8

vision:
  fps: 30
  camera_mode: hardware         # only "hardware" is implemented (see below)

database:
  retention_days: 30            # 0 = keep everything forever

system:
  station_id: STATION_01
```

A missing or malformed file falls back to built-in defaults (logged loudly,
never silently) so a typo can never prevent the system from starting.
Individual values can also be overridden per-deploy with environment
variables of the form `DWS_<SECTION>_<KEY>`, e.g. `DWS_SCALE_BAUDRATE=19200`.

**Known limitation:** `vision.camera_mode: simulation` is accepted (so it
won't crash config loading) but is **not implemented** — `VisionNodeRealSense`
only drives real RealSense hardware today. Setting it logs a warning and the
node runs against hardware regardless.

---

# Health Monitoring

`GET http://localhost:8000/health` returns a composite, *honest* status —
each field reflects a real check performed in the last
`health.check_interval_seconds` (default 5s) by `HealthMonitor`
(`dws_ros2/health_monitor.py`), running inside the main backend process:

| Field | What it actually checks |
|---|---|
| `camera` | Live frame read from the RealSense (depth + color streams alive) |
| `scale` | Scale has produced a reading within the last 2s |
| `database` | Direct `SELECT 1` against the live SQLite connection |
| `disk` | `shutil.disk_usage()` against the capture volume vs. `health.min_free_disk_gb` |
| `calibration` | Required `.npy` files present, loadable, and structurally valid |

If the backend hasn't run since the database was created (or hasn't
completed a check cycle yet), `/health` reports `"status": "unknown"`
rather than fabricating a healthy result. If a snapshot exists but is older
than ~12 missed cycles, the response sets `"stale": true`.

The HMI status dots (Network / Scale / Vision, top bar) poll this endpoint
every 5 seconds and reflect the real component state — they no longer just
mirror the WebSocket connection.

---

# Disk Retention

`dws_ros2/disk_retention.py` runs as a background thread inside the main
backend process. Once an hour (and once shortly after startup) it deletes
capture images and point clouds older than `database.retention_days` from
`captures/` and `captures/pointclouds/`. It never touches the SQLite
database itself — old transaction rows remain forever; only the on-disk
files they reference are pruned, so a request for an old image/pointcloud
will 404 once it's outside the retention window. Set
`database.retention_days: 0` to disable pruning entirely.

---

# HMI Access

Main HMI:

```text
http://localhost:8080
```

MJPEG Stream:

```text
http://localhost:8090/video_feed
```

---

# Current Capabilities

✅ Live RealSense streaming  
✅ Parcel locking  
✅ Weight synchronization  
✅ Barcode triggering  
✅ Snapshot archival  
✅ Transaction logging  
✅ HMI synchronization  
✅ WebSocket updates  
✅ MJPEG streaming  
✅ FSM orchestration  
✅ Headless startup / systemd auto-restart (Build 001)  
✅ Central YAML configuration, no code edits (Build 002)  
✅ Honest composite `/health` (camera, scale, db, disk, calibration) (Build 002)  
✅ Disk retention for captures/pointclouds (Build 002)  

---

# Future Improvements

- Multi-camera fusion
- Automatic calibration UI
- Conveyor integration
- PLC communication
- MQTT integration
- Cloud sync
- Analytics dashboard
- AI-based parcel classification
- Multi-object tracking

---

# Author

Ashmit Singh

Electronics Engineering (VLSI Design & Technology)  
Vellore Institute of Technology

Specializations:
- Robotics
- Embedded Systems
- Industrial Vision
- ROS2 Systems
- Real-Time Automation
- Control Systems

GitHub:
https://github.com/ashmitsingh7

LinkedIn:
https://www.linkedin.com/in/ashmitsingh7

---

# Disclaimer

This repository represents an active industrial vision system under continuous development and testing.
Some modules are experimental and optimized specifically for real-time warehouse automation workflows.
