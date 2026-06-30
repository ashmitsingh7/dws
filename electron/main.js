'use strict';

/**
 * DWS Desktop — Electron Main Process  (Sprint 2 — production)
 *
 * Boot sequence
 *   1  Single-instance lock
 *   2  Logger initialised (file + console)
 *   3  Splash window shown immediately
 *   4  System tray created
 *   5  ProcessSupervisor + HealthMonitor wired
 *   6  Backend processes spawned (progress → splash)
 *   7  API readiness poll (30 s timeout)
 *   8  Health polling started
 *   9  Main BrowserWindow loads http://localhost:8080
 *  10  Splash closed
 *
 * IPC handlers are registered in ipc_handlers.js.
 * Logging is centralised in logger.js.
 */

const { app, dialog, shell } = require('electron');
const path = require('path');
const fs   = require('fs');
const { execSync } = require('child_process');

// Logger must be required before everything else so uncaught exceptions are captured.
const log = require('./logger');

const ProcessSupervisor              = require('./process_supervisor');
const WindowManager                  = require('./window_manager');
const { createTray, updateTray, destroyTray } = require('./tray');
const HealthMonitor                  = require('./health_monitor');
const { registerIpcHandlers }        = require('./ipc_handlers');

// ─── Single-instance lock ─────────────────────────────────────────────────────
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  log.info('Second instance attempted — focusing existing window and exiting.');
  app.quit();
  process.exit(0);
}

// ─── Globals ──────────────────────────────────────────────────────────────────
let supervisor    = null;
let windowManager = null;
let healthMonitor = null;
let tray          = null;
let _quitting     = false;

// ─── CLI flags ────────────────────────────────────────────────────────────────
const IS_DEV = process.argv.includes('--dev');
const NO_HW  = process.argv.includes('--no-hardware');

// ─── Resolve backend root ─────────────────────────────────────────────────────
function resolveBackendRoot() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'dws_ws');
  }
  return path.resolve(__dirname, '..', 'dws_ws');
}

const BACKEND_ROOT = resolveBackendRoot();

/**
 * Resolve which python3 interpreter to launch the backend with.
 *
 * Preference order:
 *   1. An already-activated venv (VIRTUAL_ENV env var) — covers a developer
 *      running `npm run start:dev` from inside their own activated venv.
 *   2. BACKEND_ROOT/.venv/bin/python3 — the workspace-local venv created by
 *      the documented `python3 -m venv .venv` setup step, if it exists.
 *   3. `python3` on PATH — covers packaged builds / target machines where
 *      dependencies were installed system-wide rather than into a venv
 *      that happens to live at this exact path.
 *
 * This does not invent a *new* runtime strategy — it just stops hard
 * failing when step 2's specific path doesn't exist, which previously
 * left VENV_PYTHON pointing at a nonexistent file with no fallback.
 */
function resolvePython() {
  if (process.env.VIRTUAL_ENV) {
    return path.join(process.env.VIRTUAL_ENV, 'bin', 'python3');
  }

  const workspaceVenvPython = path.join(BACKEND_ROOT, '.venv', 'bin', 'python3');
  if (fs.existsSync(workspaceVenvPython)) {
    return workspaceVenvPython;
  }

  try {
    const found = execSync('command -v python3', { encoding: 'utf8' }).trim();
    if (found) return found;
  } catch (_) { /* fall through to bare 'python3' below */ }

  return 'python3';
}

const VENV_PYTHON = resolvePython();

// ─── Second-instance handler ──────────────────────────────────────────────────
app.on('second-instance', () => {
  const win = windowManager?.getMainWindow();
  if (win) {
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
  }
});

// ─── App ready ────────────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  log.info(`DWS Desktop starting — version ${app.getVersion()} build 005.1`);
  log.info(`Backend root: ${BACKEND_ROOT}`);
  log.info(`venv Python:  ${VENV_PYTHON}`);
  log.info(`Flags: dev=${IS_DEV} no-hardware=${NO_HW}`);

  // ── 1. Window manager (shows splash immediately) ──────────────────────────
  windowManager = new WindowManager({ isDev: IS_DEV });
  await windowManager.showSplash();
  sendProgress(1, 9, 'Initialising DWS…');

  // ── 2. System tray ────────────────────────────────────────────────────────
  tray = createTray({
    onOpen:     () => windowManager.showMain(),
    onRestart:  () => {
      log.info('Tray: restart services');
      supervisor?.restartAll().catch(err => log.error('Restart failed:', err));
    },
    onLogs:     () => openLogsDirectory(),
    onShutdown: () => gracefulQuit(),
    onExit:     () => gracefulQuit(),
  });
  sendProgress(2, 9, 'System tray ready…');

  // ── 3. Process supervisor ─────────────────────────────────────────────────
  supervisor = new ProcessSupervisor({
    backendRoot: BACKEND_ROOT,
    venvPython:  VENV_PYTHON,
    noHardware:  NO_HW,
    isDev:       IS_DEV,
    log:         log.supervisor,
    onStatusChange: (status) => {
      updateTray(tray, status);
      windowManager.sendToRenderer('supervisor:status', status);
    },
  });

  supervisor.on('crashed', ({ name, count }) => {
    log.error(`Process '${name}' crashed ${count} times — giving up`);
    dialog.showMessageBox({
      type:    'error',
      title:   'DWS — Process Crashed',
      message: `The ${name} process has crashed repeatedly.`,
      detail:  `It crashed ${count} times within 60 seconds and will not be restarted automatically.\n\nUse Tray → Restart Services to try again, or check logs.`,
      buttons: ['View Logs', 'OK'],
    }).then(({ response }) => {
      if (response === 0) openLogsDirectory();
    });
  });

  supervisor.on('fatal', ({ reason, message }) => {
    log.error(`Supervisor fatal: ${reason} — ${message}`);
    dialog.showMessageBox({
      type:    'error',
      title:   'DWS — Startup Blocked',
      message: 'The backend and API processes were not started.',
      detail:  `${message}\n\nThe HMI window may still load, but live data will not be available until this is resolved. Check logs for details.`,
      buttons: ['View Logs', 'OK'],
    }).then(({ response }) => {
      if (response === 0) openLogsDirectory();
    });
  });

  // ── 4. Health monitor ─────────────────────────────────────────────────────
  healthMonitor = new HealthMonitor({
    apiBase:  'http://localhost:8000',
    wsUrl:    'ws://localhost:8765',
    mjpegUrl: 'http://localhost:8090/video_feed',
    log:      log.health,
    onHealth: (health) => {
      updateTray(tray, health);
      windowManager.sendToRenderer('health:update', health);
    },
  });

  // ── 5. Register all IPC handlers ──────────────────────────────────────────
  registerIpcHandlers({ supervisor, healthMonitor, windowManager, gracefulQuit, log });
  sendProgress(3, 9, 'IPC bridge ready…');

  // ── 6. Start backend processes ────────────────────────────────────────────
  sendProgress(4, 9, 'Starting DWS backend…');
  try {
    await supervisor.startAll();
  } catch (err) {
    log.error('Backend startup failed:', err);
    await dialog.showErrorBox(
      'DWS — Startup Error',
      `Backend failed to start:\n${err.message}\n\nCheck logs at:\n${getLogsDir()}`
    );
    app.exit(1);
    return;
  }

  sendProgress(5, 9, 'Backend processes running…');

  // ── 7. Wait for API readiness ─────────────────────────────────────────────
  sendProgress(6, 9, 'Waiting for API…');
  const ready = await waitForApi('http://localhost:8000/health', 30_000, (elapsed) => {
    const remaining = Math.round((30_000 - elapsed) / 1000);
    sendProgress(6, 9, `Waiting for API… (${remaining}s)`);
  });

  if (!ready) {
    log.warn('API did not become ready within 30 s');
    const { response } = await dialog.showMessageBox({
      type:      'warning',
      title:     'DWS — Services Not Ready',
      message:   'Backend services did not respond within 30 seconds.',
      detail:    'The application will open anyway. Hardware may not be available until services finish starting.\n\nCheck logs if the problem persists.',
      buttons:   ['Open Anyway', 'View Logs', 'Quit'],
      defaultId: 0,
    });
    if (response === 2) { await gracefulQuit(); return; }
    if (response === 1) { openLogsDirectory(); await gracefulQuit(); return; }
  } else {
    log.info('API ready');
  }

  // ── 8. Start health polling ───────────────────────────────────────────────
  healthMonitor.start();
  sendProgress(7, 9, 'Health monitoring started…');

  // ── 9. Show main window ───────────────────────────────────────────────────
  sendProgress(8, 9, 'Loading HMI…');
  await windowManager.showMain();
  sendProgress(9, 9, 'Ready');

  log.info('DWS Desktop fully started');
});

// ─── Prevent default quit when all windows closed ─────────────────────────────
app.on('window-all-closed', (e) => {
  e.preventDefault(); // Stay alive in tray
});

// macOS dock click
app.on('activate', () => {
  windowManager?.showMain();
});

// ─── Graceful shutdown ────────────────────────────────────────────────────────
async function gracefulQuit() {
  if (_quitting) return;
  _quitting = true;
  log.info('Graceful shutdown initiated');

  try {
    healthMonitor?.stop();
    log.info('Health monitor stopped');
  } catch (e) { log.warn('Health monitor stop error:', e); }

  try {
    destroyTray(tray);
    log.info('Tray destroyed');
  } catch (e) { log.warn('Tray destroy error:', e); }

  try {
    windowManager?.closeAll();
    log.info('Windows closed');
  } catch (e) { log.warn('Window close error:', e); }

  try {
    await supervisor?.stopAll();
    log.info('Backend processes stopped');
  } catch (e) { log.warn('Supervisor stop error:', e); }

  log.info('DWS Desktop exiting cleanly');
  app.exit(0);
}

// Handle OS shutdown signals
process.on('SIGTERM', () => {
  log.info('SIGTERM received');
  gracefulQuit();
});
process.on('SIGINT', () => {
  log.info('SIGINT received');
  gracefulQuit();
});

// ─── Helpers ──────────────────────────────────────────────────────────────────

function getLogsDir() {
  return path.join(app.getPath('home'), '.dws', 'logs');
}

function openLogsDirectory() {
  const dir = getLogsDir();
  if (fs.existsSync(dir)) {
    shell.openPath(dir);
  } else {
    log.warn(`openLogsDirectory: directory does not exist: ${dir}`);
  }
}

/** Send a startup:progress event to the splash/renderer. */
function sendProgress(step, total, message) {
  log.info(`Startup [${step}/${total}] ${message}`);
  windowManager?.setSplashStatus(message);
  windowManager?.sendToRenderer('startup:progress', { step, total, message });
}

/**
 * Poll GET url every 1 s until a 2xx response or timeoutMs elapses.
 * onTick(elapsedMs) is called on each retry so the splash can show a countdown.
 */
async function waitForApi(url, timeoutMs, onTick) {
  const start    = Date.now();
  const deadline = start + timeoutMs;

  while (Date.now() < deadline) {
    try {
      const { default: fetch } = await import('node-fetch');
      const res = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (res.ok) return true;
    } catch (_) { /* still starting */ }

    await sleep(1000);
    if (onTick) onTick(Date.now() - start);
  }
  return false;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// Export for ipc_handlers to call on app:quit
module.exports = { gracefulQuit };