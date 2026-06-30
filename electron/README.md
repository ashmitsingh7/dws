# DWS Desktop

Electron shell for the DWS Dimensional Weighing System.  
Wraps the existing Python/FastAPI/ROS2 backend in a professional industrial desktop application.

---

## Quick start (development)

```bash
# 1. Run the DWS installer first (creates venv, builds colcon workspace)
sudo bash ../dws_ws/src/dws_ros2/deploy/install.sh --dev

# 2. Install Electron dependencies
cd desktop
npm install

# 3. Generate placeholder icons (replace with real artwork before shipping)
node scripts/make_placeholder_icons.js

# 4. Launch
npm start
# or:
./scripts/launch.sh --dev --no-hardware
```

The application will:
1. Show a splash screen immediately  
2. Spawn the three backend processes (`run_dws`, `uvicorn`, `http.server`)  
3. Wait up to 30 s for the API to respond  
4. Load the existing HMI at `http://localhost:8080` inside a BrowserWindow  
5. Show a system tray icon reflecting live health  

---

## CLI flags

| Flag             | Effect                                         |
|------------------|------------------------------------------------|
| *(none)*         | Production mode — full hardware expected       |
| `--dev`          | Open DevTools, verbose console logging         |
| `--no-hardware`  | Skip RealSense / scale hardware checks         |

---

## npm scripts

| Script                 | Effect                                     |
|------------------------|--------------------------------------------|
| `npm start`            | Launch in production mode                  |
| `npm run start:dev`    | Launch with `--dev` flag                   |
| `npm run start:no-hw`  | Launch with `--no-hardware`                |
| `npm run make-icons`   | Generate placeholder PNG icons             |
| `npm run build`        | Build `.deb` + `.AppImage` for Linux x64   |
| `npm run build:deb`    | Build `.deb` only                          |
| `npm run build:appimage` | Build `.AppImage` only                   |

---

## File layout

```
desktop/
├── main.js                  Electron main process — boot sequence, IPC, shutdown
├── preload.js               contextBridge → window.dws (renderer API)
├── ipc_handlers.js          All ipcMain.handle() registrations
├── process_supervisor.js    Backend process spawn / monitor / crash-restart
├── window_manager.js        Splash → BrowserWindow lifecycle
├── tray.js                  System tray icon + context menu
├── health_monitor.js        HTTP + WebSocket health polling
├── logger.js                electron-log wrapper, rotation, sub-loggers
├── splash.html              Branded startup screen with step progress bar
├── package.json             npm manifest + electron-builder config
├── electron-builder.yml     Packaging configuration
├── scripts/
│   ├── launch.sh            Dev launcher (auto npm install, auto icons)
│   ├── build.sh             Production build with preflight checks
│   └── make_placeholder_icons.js  Generate PNG stubs for tray + app
└── assets/
    └── icons/
        ├── icon.png         512×512 app icon (REQUIRED before build)
        ├── tray-ok.png      22×22 — all systems healthy
        ├── tray-warn.png    22×22 — partial degradation
        ├── tray-error.png   22×22 — backend down
        └── tray-idle.png    22×22 — not yet started
```

---

## Log files

All logs land in `~/.dws/logs/`:

| File              | Content                              |
|-------------------|--------------------------------------|
| `main.log`        | Electron main process (startup, IPC) |
| `supervisor.log`  | Process spawn / exit / crash events  |
| `health.log`      | Health poll results                  |
| `backend.log`     | `run_dws` stdout + stderr            |
| `api.log`         | `uvicorn` stdout + stderr            |
| `hmi.log`         | `http.server` stdout + stderr        |

Logs rotate at 10 MB; 5 archives kept per file.  
Access via **Tray → View Logs** or `~/.dws/logs/`.

---

## IPC channels

### Renderer → Main (`window.dws.*`)

| Method                   | Description                           |
|--------------------------|---------------------------------------|
| `startBackend()`         | Start all backend processes           |
| `stopBackend()`          | Stop all backend processes            |
| `restartBackend()`       | Restart all backend processes         |
| `getSupervisorStatus()`  | Returns `ProcessStatus`               |
| `getHealth()`            | Returns `HealthSnapshot`              |
| `getVersion()`           | `{ app, build, electron, node }`      |
| `getLogsDir()`           | Path string to logs directory         |
| `openLogs()`             | Open logs dir in file manager         |
| `quit()`                 | Graceful application exit             |
| `setKiosk(boolean)`      | Enable/disable kiosk/fullscreen mode  |
| `getKiosk()`             | Returns current kiosk state           |
| `minimize()`             | Minimise main window                  |
| `maximizeToggle()`       | Toggle maximise/restore               |
| `hideToTray()`           | Hide main window to tray              |

### Main → Renderer (event subscriptions)

| Method                          | Payload           | Frequency        |
|---------------------------------|-------------------|------------------|
| `onSupervisorStatus(cb)`        | `ProcessStatus`   | On change        |
| `onHealthUpdate(cb)`            | `HealthSnapshot`  | Every 5 s        |
| `onStartupProgress(cb)`         | `{step,total,message}` | Boot only  |

All subscription methods return an `unsubscribe()` function.

---

## Systemd co-existence

If `dws@$USER.service` is already running, the Electron app detects live PID
files in `~/.dws/run/` and enters **monitor-only mode**: it displays the HMI
from the existing instance without spawning or killing any processes.

---

## Building for production

```bash
# Prerequisites
sudo bash ../dws_ws/src/dws_ros2/deploy/install.sh   # full install
npm install

# Replace placeholder icons with real artwork
# assets/icons/icon.png  (512×512)
# assets/icons/tray-*.png (22×22)

# Build
./scripts/build.sh

# Install .deb on Ubuntu
sudo dpkg -i dist/dws_1.0.0_amd64.deb
```

The `.deb` installs a `.desktop` file; DWS appears in the application menu.  
The Python venv is **not** bundled — `install.sh` must be run on the target
machine before first launch.
