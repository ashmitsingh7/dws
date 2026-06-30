# DWS Desktop — Electron Implementation Checklist

**Sprint 1 baseline:** Desktop Application Infrastructure  
**Date prepared:** 2026-06-25  
**Next sprint owner:** Desktop Application Engineer

Items marked ✅ are complete as of Sprint 1.  
Items marked ☐ are remaining work, ordered by dependency.

---

## Sprint 1 — Completed ✅

- ✅ `desktop/package.json` — npm manifest, electron-builder config reference, scripts
- ✅ `desktop/main.js` — app lifecycle, single-instance lock, startup/shutdown, IPC handlers
- ✅ `desktop/preload.js` — contextBridge `window.dws` API, all IPC channels documented
- ✅ `desktop/process_supervisor.js` — spawn / monitor / crash-restart / systemd detect / stop
- ✅ `desktop/window_manager.js` — splash, main window, kiosk mode, bounds persistence
- ✅ `desktop/tray.js` — system tray, context menu, icon status, all 5 menu items
- ✅ `desktop/health_monitor.js` — HTTP /health poll, WebSocket probe, HealthSnapshot emission
- ✅ `desktop/splash.html` — branded frameless splash with animated progress bar
- ✅ `desktop/electron-builder.yml` — Linux .deb + AppImage targets, extraResources config
- ✅ `desktop/assets/icons/README.md` — icon specification
- ✅ `ELECTRON_ARCHITECTURE.md` — process diagram, startup/shutdown/crash flows, IPC tables
- ✅ `ELECTRON_IMPLEMENTATION_CHECKLIST.md` — this document

---

## Sprint 2 — Remaining implementation tasks

### 2.1  Icon assets  ☐ BLOCKING BUILD

- ☐ Design 512×512 `icon.png` using the box+scan-line SVG from `splash.html` as source
- ☐ Export 22×22 `tray-ok.png` (accent blue #00B4D8)
- ☐ Export 22×22 `tray-warn.png` (amber #F59E0B)
- ☐ Export 22×22 `tray-error.png` (red #EF4444)
- ☐ Export 22×22 `tray-idle.png` (grey #4A5568)
- ☐ Place all files in `desktop/assets/icons/` before `npm run build`

### 2.2  npm bootstrap

- ☐ `cd desktop && npm install` on the development machine
- ☐ Verify `node_modules/electron`, `electron-builder`, `electron-log`, `electron-store`, `node-fetch` are installed
- ☐ Confirm `npm start` launches the app against the running backend

### 2.3  Development smoke-test  (requires local DWS install)

- ☐ `npm start` opens splash screen within 1 s
- ☐ Backend processes start (visible via `ps aux | grep dws`)
- ☐ Splash transitions to main window loading HMI at `http://localhost:8080`
- ☐ System tray icon appears with correct idle → ok transition
- ☐ Tray → Open DWS brings window to foreground
- ☐ Tray → View Logs opens `~/.dws/logs/` in file manager
- ☐ Tray → Restart Services restarts backend; HMI reconnects
- ☐ Tray → Exit shuts down cleanly; no orphan Python processes remain
- ☐ Closing main window hides it (not destroy); tray → Open restores it
- ☐ Launching a second instance focuses the existing window
- ☐ Window size/position is remembered across restarts

### 2.4  Health monitor validation

- ☐ Kill `uvicorn` manually — tray icon changes to error within 5 s
- ☐ Kill WebSocket port process — tray shows warn within 15 s
- ☐ `/health` endpoint returns full payload — camera / db / disk / calibration fields visible
- ☐ Confirm `HealthSnapshot` fields map correctly to `health_monitor.py`'s JSON keys
  - `camera_status`, `db_status`, `disk_ok`, `calibration_status`
  - Update `health_monitor.js` key mappings if backend field names differ

### 2.5  Crash recovery validation

- ☐ `kill -9 <backend_pid>` — Electron restarts it within `RESTART_BASE_DELAY_MS` (2 s)
- ☐ Kill backend 5 times within 60 s — Electron stops retrying, shows dialog
- ☐ Confirm crash timestamps reset after a successful restart longer than `CRASH_WINDOW_MS` apart

### 2.6  Systemd co-existence test

- ☐ Start `dws@$USER.service` via systemd
- ☐ Launch Electron — confirm it enters monitor-only mode (log line: `entering monitor-only mode`)
- ☐ Tray "Restart Services" should be disabled or show a tooltip in monitor-only mode
  - ☐ Add `enabled: false` guard in `tray.js` when `supervisor._monitorOnly === true`
- ☐ Quit Electron — confirm systemd service is still running

### 2.7  Kiosk mode

- ☐ Test `npm start -- --kiosk` (or `npm start` after `store.set('kiosk', true)`)
- ☐ Confirm window is fullscreen and not escapable without keyboard shortcut
- ☐ Define and document the kiosk exit shortcut (e.g. `Ctrl+Alt+Q` in main.js accelerator)
- ☐ Add kiosk toggle to Settings flow (future sprint) — store key: `kiosk` in electron-store

### 2.8  `npm run build` — packaging

- ☐ Icons present (2.1 above)
- ☐ `colcon build` completed in `dws_ws/` (generates `install/` tree for `extraResources`)
- ☐ `npm run build:deb` produces `dist/dws_1.0.0_amd64.deb`
- ☐ `npm run build:appimage` produces `dist/DWS-1.0.0.AppImage`
- ☐ Install `.deb` on a clean Ubuntu 24.04 VM and confirm app icon appears in app drawer
- ☐ Confirm `install.sh` (install guide Step 2) is run **before** launching the packaged app
  - The packaged Electron app does **not** install Python deps — that is `install.sh`'s job

---

## Sprint 3 — Future hardening tasks

### 3.1  Electron security hardening

- ☐ Audit all `webPreferences` — confirm `sandbox: false` is still needed for preload or switch to `contextIsolation`-only approach
- ☐ Set `Content-Security-Policy` header for any Electron-native HTML pages (splash.html already doesn't load remote scripts)
- ☐ Ensure `webSecurity: true` (already set in window_manager.js) — do not downgrade
- ☐ Review `allowRunningInsecureContent: false` — keep false

### 3.2  Auto-update

- ☐ Set up an update server (Nexus, S3, or a simple HTTP file server on the internal network)
- ☐ Add `publish` config to `electron-builder.yml`
- ☐ Add `electron-updater` dependency to `package.json`
- ☐ Hook `autoUpdater` events in `main.js` — notify via tray, not intrusive dialog
- ☐ Decide backend update strategy: update Electron shell and Python backend atomically, or separately
  - Backend schema migrations (`database_node.py`) must run **before** new backend code starts
  - Recommended: Electron downloads new backend bundle, runs `python3 -m dws_ros2.migrate`, then restarts

### 3.3  First-run / onboarding wizard

- ☐ Detect missing `.npy` calibration files on startup
- ☐ Show a native dialog guiding operator to open HMI → Settings → Calibrate
- ☐ Alternatively: create a separate `CalibrationWindow` (BrowserWindow loading a subset of the HMI)
- ☐ Store first-run completion flag in electron-store: `firstRunComplete: true`

### 3.4  Crash reporting (optional)

- ☐ Integrate `electron-log` for structured Electron-side logging
- ☐ Hook `process.on('uncaughtException', ...)` in main.js for JS-side crashes
- ☐ Hook `ProcessSupervisor 'crashed'` event to write a crash report to `~/.dws/logs/crashes.json`
- ☐ Add "Send logs" action to tray (zip `~/.dws/logs/` and open file manager or email client)

### 3.5  Authentication integration point

- ☐ Design `AuthWindow` BrowserWindow with its own preload
- ☐ Add `ipcMain.handle('auth:login', ...)` handler in `main.js`
- ☐ After successful auth, load HMI with session cookie or `localStorage` token injection
- ☐ See ELECTRON_ARCHITECTURE.md §10 for the full integration approach

### 3.6  Windows support (only if a customer requirement emerges)

- ☐ Spike `pyrealsense2` + `open3d` + `torch` install on Windows (Intel RealSense Windows SDK path differs)
- ☐ Replace systemd-managed startup detection with Windows Service / NSSM equivalent
- ☐ Add `win` target to `electron-builder.yml` (NSIS installer)
- ☐ Test serial port enumeration on Windows (COM port vs /dev/ttyUSB0)

---

## Backend prerequisites for Electron (not Electron work, but blocking it)

These are items identified in the Productization Analysis that must be resolved
before the Electron packaging is reliable.  They belong to other sprint owners
but are listed here because they affect the Electron integration path.

- ☐ **Write `install.sh`** (installs apt deps, RealSense SDK, venv, pip, colcon build)
  - Electron packaging assumes `install.sh` has run on the target machine
  - Electron does NOT bundle the Python venv
- ☐ **Reconcile `setup.py` `install_requires` vs `README.md` package list**
  - Installer built against the wrong list will silently fail on first `import`
- ☐ **Make HMI WebSocket + MJPEG URLs derive from `window.location`**
  - Currently hardcoded to `localhost` — latent bug if HMI is ever loaded from `file://`
  - See Productization Analysis §3.1 for the two affected lines in `hmi/index.html`
- ☐ **Spike native dep bundling** (`pyrealsense2`, `open3d`, `torch`) in a CI VM
  - Determine unattended install time and total disk requirement
  - This determines whether "one-click install" is a realistic promise

---

*Checklist maintained by: Desktop Application Engineer*  
*Architecture reference: `ELECTRON_ARCHITECTURE.md`*
