# DWS Desktop — Electron Architecture

**Version:** DWS v1.0 Build 005.1  
**Sprint:** 1 — Desktop Application Infrastructure  
**Status:** Implementation complete (Sprint 1 scope)

---

## 1. Overview

The DWS desktop application wraps the existing browser-based HMI inside an
Electron shell.  The existing Python backend, FastAPI server, WebSocket node,
and HMI (`hmi/index.html`) are **unchanged**.  Electron provides:

- Automatic backend process lifecycle management (start / monitor / restart / stop)
- A professional operator experience: no browser, no terminal
- System tray with live health status
- Crash recovery with exponential back-off
- A clean, documented IPC boundary for future feature additions

---

## 2. Process diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│  OS (Ubuntu 22.04 / 24.04)                                              │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  Electron Main Process (Node.js)                                  │  │
│  │                                                                   │  │
│  │  ┌────────────────┐  ┌──────────────────┐  ┌─────────────────┐   │  │
│  │  │ProcessSupervisor│  │  WindowManager   │  │  HealthMonitor  │   │  │
│  │  │                │  │                  │  │                 │   │  │
│  │  │ spawn / kill   │  │ splash → main    │  │ HTTP poll 5s    │   │  │
│  │  │ crash-restart  │  │ bounds persist   │  │ WS probe 15s    │   │  │
│  │  │ systemd detect │  │ kiosk mode       │  │                 │   │  │
│  │  └───────┬────────┘  └────────┬─────────┘  └────────┬────────┘   │  │
│  │          │                    │                       │            │  │
│  │          └──────────┬─────────┘                       │            │  │
│  │                     │  ipcMain                        │            │  │
│  │                     │  handlers                       │            │  │
│  │                     ▼                                 │            │  │
│  │  ┌────────────────────────────────────────┐           │            │  │
│  │  │  Electron Renderer (BrowserWindow)     │           │            │  │
│  │  │                                        │◄──────────┘            │  │
│  │  │  hmi/index.html (http://localhost:8080)│  health:update         │  │
│  │  │  — unchanged —                         │  supervisor:status     │  │
│  │  │                                        │                        │  │
│  │  │  window.dws (contextBridge)            │                        │  │
│  │  └──────────────────┬─────────────────────┘                        │  │
│  │                     │ ipcRenderer.invoke                           │  │
│  └─────────────────────┼───────────────────────────────────────────────┘  │
│                        │                                                 │
│  ┌─────────────────────▼───────────────────────────────────────────────┐  │
│  │  DWS Python Backend (3 processes, managed by ProcessSupervisor)     │  │
│  │                                                                     │  │
│  │  ┌──────────────────────┐  ┌────────────────────┐  ┌────────────┐  │  │
│  │  │  run_dws.py          │  │  api_server.py      │  │ http.server│  │  │
│  │  │  (9 SimBus nodes)    │  │  uvicorn :8000      │  │  :8080     │  │  │
│  │  │  WS :8765            │  │  FastAPI REST       │  │  HMI HTML  │  │  │
│  │  │  MJPEG :8090         │  │  /health endpoint   │  │            │  │  │
│  │  └──────────┬───────────┘  └────────┬────────────┘  └─────┬──────┘  │  │
│  │             │                       │                       │         │  │
│  │             └───────────────────────┴───────────────────────┘         │  │
│  │                              SQLite (dws_runtime/database/dws_data.db)│  │
│  └─────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Startup flow

```
app.whenReady()
│
├─ 1. WindowManager.showSplash()
│      → frameless 480×320 window, "Initialising…"
│
├─ 2. createTray()
│      → tray icon: idle grey
│
├─ 3. new ProcessSupervisor(opts)
│
├─ 4. new HealthMonitor(opts)
│
├─ 5. ProcessSupervisor.startAll()
│      │
│      ├─ _isSystemdManaged()?
│      │    YES → monitor-only mode (attach to existing PIDs)
│      │    NO  → spawn backend → (2 s pause) → spawn api → spawn hmi
│      │
│      └─ emitStatus() → tray updated, splash updated
│
├─ 6. waitForApi('http://localhost:8000/health', 30 s)
│      → polls every 1 s; fails gracefully with dialog
│
├─ 7. HealthMonitor.start()
│      → HTTP poll every 5 s, WS probe every 15 s
│
└─ 8. WindowManager.showMain()
       → BrowserWindow loads http://localhost:8080
       → ready-to-show → show window, close splash
       → flush pending IPC messages
```

---

## 4. Shutdown flow

```
gracefulQuit()  [triggered by: tray Exit, IPC app:quit, SIGTERM, SIGINT]
│
├─ 1. HealthMonitor.stop()        — stop polling timers
├─ 2. destroyTray(tray)           — remove tray icon
├─ 3. WindowManager.closeAll()    — close BrowserWindow(s)
├─ 4. ProcessSupervisor.stopAll() — SIGTERM to hmi → api → backend
│      → wait up to 5 s each for graceful exit
│      → SIGKILL if grace period exceeded
└─ 5. app.exit(0)
```

---

## 5. Crash recovery

```
process exits unexpectedly (exitCode ≠ 0, signal ≠ SIGTERM/SIGINT)
│
└─ _maybeCrashRestart(name, code, signal)
     │
     ├─ Record crash timestamp in rolling window (60 s)
     │
     ├─ crashCount ≤ MAX_CRASHES (5)?
     │    YES → schedule restart with exponential backoff
     │           attempt 1:  2 s
     │           attempt 2:  4 s
     │           attempt 3:  8 s
     │           attempt 4: 16 s
     │           attempt 5: 30 s (capped)
     │
     └─ crashCount > MAX_CRASHES?
          → emit 'crashed' event (main.js can show a dialog / notification)
          → stop retrying
          → tray icon → error red
```

---

## 6. IPC diagram

### Renderer → Main  (`ipcRenderer.invoke` → `ipcMain.handle`)

| Channel               | Payload         | Returns              | Description                      |
|-----------------------|-----------------|----------------------|----------------------------------|
| `supervisor:start`    | —               | `void`               | Start all backend processes      |
| `supervisor:stop`     | —               | `void`               | Stop all backend processes       |
| `supervisor:restart`  | —               | `void`               | Restart all backend processes    |
| `supervisor:status`   | —               | `ProcessStatus`      | Current process state snapshot   |
| `health:get`          | —               | `HealthSnapshot`     | Latest health check result       |
| `app:version`         | —               | `{app, build}`       | App and build version strings    |
| `app:logs-dir`        | —               | `string`             | Absolute path to logs directory  |
| `app:open-logs`       | —               | `void`               | Open logs dir in file manager    |
| `app:quit`            | —               | `void`               | Graceful application quit        |

### Main → Renderer  (`webContents.send` → `ipcRenderer.on`)

| Channel               | Payload          | Frequency            | Description                         |
|-----------------------|------------------|----------------------|-------------------------------------|
| `supervisor:status`   | `ProcessStatus`  | On change            | Process state changed               |
| `health:update`       | `HealthSnapshot` | Every 5 s (HTTP poll)| Updated health check result         |

### contextBridge  (`window.dws.*`)

All IPC channels above are exposed to the HMI renderer via `window.dws`
(defined in `preload.js`).  The existing `hmi/index.html` does **not** call
`window.dws` — it communicates with the backend directly via HTTP and
WebSocket as before.  `window.dws` is available for future native UI overlays
without modifying the existing HMI.

---

## 7. Backend launch sequence

```
ProcessSupervisor._spawnProcess('backend')
│
├─ Resolve venv Python:  dws_ws/.venv/bin/python3
├─ Locate ROS setup:     /opt/ros/jazzy/setup.bash  (or humble)
├─ Check colcon install: dws_ws/install/setup.bash
│
├─ If both ROS + colcon present:
│    bash -c "source /opt/ros/jazzy/setup.bash &&
│             source dws_ws/install/setup.bash &&
│             ros2 run dws_ros2 run_dws [--no-hardware] [--dev]"
│
└─ Else (development / unbuilt tree):
     .venv/bin/python3 -m dws_ros2.run_dws [flags]
     cwd: dws_ws/src/dws_ros2/

→ Wait 2 s
→ _spawnProcess('api')
     .venv/bin/python3 -m uvicorn api_server:app --host 0.0.0.0 --port 8000
     cwd: dws_ws/src/dws_ros2/dws_ros2/

→ _spawnProcess('hmi')
     .venv/bin/python3 -m http.server 8080
     cwd: dws_ws/src/dws_ros2/hmi/
```

Stdout and stderr for each process are piped to:
- `~/.dws/logs/backend.log`
- `~/.dws/logs/api.log`
- `~/.dws/logs/hmi.log`

---

## 8. Electron lifecycle (app events)

| Event                  | Handler                                              |
|------------------------|------------------------------------------------------|
| `app.whenReady()`      | Full startup sequence (§3)                          |
| `second-instance`      | Focus existing main window (single-instance lock)   |
| `window-all-closed`    | `e.preventDefault()` — stay alive in tray           |
| `activate` (macOS)     | `windowManager.showMain()`                          |
| `SIGTERM` / `SIGINT`   | `gracefulQuit()`                                    |

---

## 9. Systemd co-existence

On a machine where `dws@<user>.service` is running (production deployment):

```
Electron starts
│
└─ ProcessSupervisor._isSystemdManaged()
     │
     ├─ Reads ~/.dws/run/backend.pid, api.pid, hmi.pid
     ├─ Sends signal 0 to each PID (non-destructive liveness check)
     │
     ├─ Any live PID found → MONITOR-ONLY mode
     │    Electron shows HMI from the running instance.
     │    ProcessSupervisor does NOT spawn or kill anything.
     │    Health polling continues normally.
     │    Tray "Restart Services" is disabled in monitor-only mode.
     │
     └─ No live PIDs → MANAGED mode
          Electron owns the full process lifecycle.
```

---

## 10. Future authentication integration point

The current HMI has no authentication.  When operator login is added:

1. **Electron main process** intercepts `BrowserWindow.loadURL()` and injects
   an auth overlay **before** the HMI loads, OR a separate `AuthWindow` is
   shown first (using a different `BrowserWindow` with its own preload).

2. The auth preload calls `ipcRenderer.invoke('auth:login', credentials)`.

3. `ipcMain.handle('auth:login', ...)` validates credentials (local DB or
   cloud API) and returns a session token.

4. On success, the main window loads the HMI; the token is passed via
   `webContents.executeJavaScript(...)` or a cookie on `localhost`.

5. The existing HMI picks up the token for subsequent API calls — minimal
   HMI change needed (just read `localStorage.getItem('dws_token')` or a
   cookie already set by the Electron shell).

**This is deliberately out of scope for Sprint 1.**  The integration point is
documented here so the IPC naming and the preload boundary do not need to
change when auth is added.

---

## 11. File inventory

```
desktop/
├── main.js                  Electron main process — startup / shutdown / IPC handlers
├── preload.js               Context bridge — window.dws API exposed to renderer
├── process_supervisor.js    Backend process lifecycle (spawn / monitor / restart)
├── window_manager.js        BrowserWindow management (splash / main / kiosk)
├── tray.js                  System tray creation and status updates
├── health_monitor.js        Continuous health polling (HTTP + WebSocket)
├── splash.html              Frameless splash screen with animated progress bar
├── electron-builder.yml     Build / packaging configuration
├── package.json             npm manifest and electron-builder config reference
└── assets/
    └── icons/
        ├── README.md        Icon specification and naming convention
        ├── icon.png         [REQUIRED] 512×512 app icon — add before build
        ├── tray-ok.png      [REQUIRED] 22×22 tray icon, healthy state
        ├── tray-warn.png    [REQUIRED] 22×22 tray icon, degraded state
        ├── tray-error.png   [REQUIRED] 22×22 tray icon, error state
        └── tray-idle.png    [REQUIRED] 22×22 tray icon, not started
```

---

*End of ELECTRON_ARCHITECTURE.md — Sprint 1*
