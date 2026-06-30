'use strict';

/**
 * DWS Desktop — IPC Handlers
 *
 * All ipcMain.handle() registrations live here.
 * main.js calls registerIpcHandlers(deps) once, after all modules are ready.
 *
 * Channel reference
 * ─────────────────────────────────────────────────────────────────────────────
 * RENDERER → MAIN (invoke / handle)
 *
 *   supervisor:start         ()        → void
 *   supervisor:stop          ()        → void
 *   supervisor:restart       ()        → void
 *   supervisor:status        ()        → ProcessStatus
 *
 *   health:get               ()        → HealthSnapshot | null
 *
 *   app:version              ()        → { app: string, build: string }
 *   app:logs-dir             ()        → string
 *   app:open-logs            ()        → void
 *   app:quit                 ()        → void
 *   app:set-kiosk            (boolean) → void
 *   app:get-kiosk            ()        → boolean
 *
 *   window:minimize          ()        → void
 *   window:maximize-toggle   ()        → void
 *   window:close             ()        → void   (hides to tray)
 *
 * MAIN → RENDERER (webContents.send)  — registered in main.js, listed here for reference
 *
 *   supervisor:status        ProcessStatus    — on any process state change
 *   health:update            HealthSnapshot   — every 5 s HTTP poll
 *   startup:progress         { step, total, message }  — during boot sequence
 */

const { ipcMain, app, shell, BrowserWindow } = require('electron');
const path = require('path');
const fs   = require('fs');
const Store = require('electron-store');

const store = new Store({ name: 'window-state' });

/**
 * @param {{
 *   supervisor:    import('./process_supervisor'),
 *   healthMonitor: import('./health_monitor'),
 *   windowManager: import('./window_manager'),
 *   gracefulQuit:  () => Promise<void>,
 *   log:           import('./logger'),
 * }} deps
 */
function registerIpcHandlers({ supervisor, healthMonitor, windowManager, gracefulQuit, log }) {

  // ── Supervisor ─────────────────────────────────────────────────────────────

  ipcMain.handle('supervisor:start', async () => {
    log.info('IPC supervisor:start');
    await supervisor.startAll();
  });

  ipcMain.handle('supervisor:stop', async () => {
    log.info('IPC supervisor:stop');
    await supervisor.stopAll();
  });

  ipcMain.handle('supervisor:restart', async () => {
    log.info('IPC supervisor:restart');
    await supervisor.restartAll();
  });

  ipcMain.handle('supervisor:status', () => {
    return supervisor.getStatus();
  });

  // ── Health ─────────────────────────────────────────────────────────────────

  ipcMain.handle('health:get', () => {
    return healthMonitor.getLatest();
  });

  // ── App ───────────────────────────────────────────────────────────────────

  ipcMain.handle('app:version', () => ({
    app:   app.getVersion(),
    build: '005.1',
    electron: process.versions.electron,
    node:     process.versions.node,
  }));

  ipcMain.handle('app:logs-dir', () => {
    return path.join(app.getPath('home'), '.dws', 'logs');
  });

  ipcMain.handle('app:open-logs', () => {
    const dir = path.join(app.getPath('home'), '.dws', 'logs');
    if (fs.existsSync(dir)) shell.openPath(dir);
    else log.warn(`IPC app:open-logs — logs dir not found: ${dir}`);
  });

  ipcMain.handle('app:quit', async () => {
    log.info('IPC app:quit received');
    await gracefulQuit();
  });

  ipcMain.handle('app:set-kiosk', (_event, enabled) => {
    log.info(`IPC app:set-kiosk → ${enabled}`);
    store.set('kiosk', Boolean(enabled));
    const win = windowManager.getMainWindow();
    if (win) {
      win.setKiosk(Boolean(enabled));
      win.setFullScreen(Boolean(enabled));
    }
  });

  ipcMain.handle('app:get-kiosk', () => {
    return store.get('kiosk', false);
  });

  // ── Window ────────────────────────────────────────────────────────────────

  ipcMain.handle('window:minimize', () => {
    const win = windowManager.getMainWindow();
    if (win) win.minimize();
  });

  ipcMain.handle('window:maximize-toggle', () => {
    const win = windowManager.getMainWindow();
    if (!win) return;
    if (win.isMaximized()) win.unmaximize();
    else win.maximize();
  });

  ipcMain.handle('window:close', () => {
    // Hide to tray, not destroy
    const win = windowManager.getMainWindow();
    if (win) win.hide();
  });

  log.info('IPC handlers registered');
}

module.exports = { registerIpcHandlers };
