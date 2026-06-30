'use strict';

/**
 * DWS Desktop — Window Manager  (Sprint 2 — production)
 *
 * Manages:
 *   - Frameless splash screen (shown at app.ready before backend starts)
 *   - Main BrowserWindow (loads http://localhost:8080 — existing HMI unchanged)
 *
 * Features:
 *   - Splash status text updated from startup sequence
 *   - Main window bounds persisted across restarts
 *   - Kiosk / fullscreen mode
 *   - HMI poll-and-reload if port 8080 not yet up at load time
 *   - Hide-to-tray on close (not destroy)
 *   - Queues IPC messages sent before main window exists
 */

const { BrowserWindow, screen } = require('electron');
const path  = require('path');
const Store = require('electron-store');

const HMI_URL      = process.env.DWS_HMI_URL || 'http://localhost:8080';
const SPLASH_FILE  = path.join(__dirname, 'splash.html');
const PRELOAD_FILE = path.join(__dirname, 'preload.js');

const store = new Store({ name: 'window-state' });

class WindowManager {
  constructor({ isDev = false, kiosk = false } = {}) {
    this._isDev  = isDev;
    this._kiosk  = kiosk || store.get('kiosk', false);
    this._splash = null;
    this._main   = null;
    this._queue  = [];          // pending IPC messages before main window exists
    this._reloadTimer = null;
  }

  // ── Splash ─────────────────────────────────────────────────────────────────

  async showSplash() {
    if (this._splash && !this._splash.isDestroyed()) return;

    this._splash = new BrowserWindow({
      width:           520,
      height:          340,
      frame:           false,
      resizable:       false,
      movable:         true,
      transparent:     false,
      alwaysOnTop:     true,
      center:          true,
      skipTaskbar:     true,
      backgroundColor: '#0F1923',
      webPreferences: {
        nodeIntegration:  false,
        contextIsolation: true,
        sandbox:          true,
      },
    });

    await this._splash.loadFile(SPLASH_FILE);
    this._splash.show();
  }

  /** Update the status text shown on the splash. Safe to call at any time. */
  setSplashStatus(message) {
    if (!this._splash || this._splash.isDestroyed()) return;
    // Use executeJavaScript — splash has no nodeIntegration so no require()
    this._splash.webContents
      .executeJavaScript(
        `typeof window.__setStatus === 'function'
          ? window.__setStatus(${JSON.stringify(message)})
          : (document.getElementById('status') && (document.getElementById('status').textContent = ${JSON.stringify(message)}))`
      )
      .catch(() => {});
  }

  // ── Main window ────────────────────────────────────────────────────────────

  async showMain() {
    if (this._main && !this._main.isDestroyed()) {
      this._main.show();
      this._main.focus();
      return;
    }

    const bounds = this._restoreBounds();

    this._main = new BrowserWindow({
      ...bounds,
      minWidth:        1024,
      minHeight:       768,
      title:           'DWS',
      backgroundColor: '#0F1923',
      show:            false,
      kiosk:           this._kiosk,
      fullscreen:      this._kiosk,
      autoHideMenuBar: !this._isDev,
      webPreferences: {
        nodeIntegration:             false,
        contextIsolation:            true,
        sandbox:                     false,   // preload needs require()
        preload:                     PRELOAD_FILE,
        webSecurity:                 true,
        allowRunningInsecureContent: false,
      },
    });

    // Persist bounds on resize / move
    if (!this._kiosk) {
      this._main.on('resize', () => this._saveBounds());
      this._main.on('move',   () => this._saveBounds());
    }

    this._main.on('ready-to-show', () => {
      this._main.show();

      if (store.get('maximized', false) && !this._kiosk) {
        this._main.maximize();
      }

      this._closeSplash();
      this._flushQueue();

      if (this._isDev) {
        this._main.webContents.openDevTools({ mode: 'detach' });
      }
    });

    // Hide to tray on close — do not destroy
    this._main.on('close', (e) => {
      if (!this._main._forceQuit) {
        e.preventDefault();
        this._main.hide();
      }
    });

    this._main.on('closed', () => {
      this._main = null;
    });

    // Load HMI — poll and reload if not up yet
    try {
      await this._main.loadURL(HMI_URL);
    } catch (_) {
      this._showConnectingPage();
      this._startPollReload();
    }

    // Also handle mid-session navigation failures (e.g. HMI restarted)
    this._main.webContents.on('did-fail-load', (_e, errCode, _errDesc, url) => {
      if (url === HMI_URL) {
        this._showConnectingPage();
        this._startPollReload();
      }
    });
  }

  getMainWindow() {
    return this._main && !this._main.isDestroyed() ? this._main : null;
  }

  /** Send IPC message to renderer; queued if window not yet ready. */
  sendToRenderer(channel, payload) {
    const win = this.getMainWindow();
    if (win && !win.webContents.isLoading()) {
      win.webContents.send(channel, payload);
    } else {
      this._queue.push({ channel, payload });
    }
  }

  /** Destroy all windows (called on quit). */
  closeAll() {
    clearInterval(this._reloadTimer);
    this._reloadTimer = null;

    if (this._main && !this._main.isDestroyed()) {
      this._main._forceQuit = true;
      this._main.close();
    }
    this._closeSplash();
  }

  // ── Bounds persistence ─────────────────────────────────────────────────────

  _saveBounds() {
    if (!this._main || this._main.isDestroyed()) return;
    if (this._main.isMaximized()) { store.set('maximized', true); return; }
    store.set('maximized', false);
    store.set('bounds', this._main.getBounds());
  }

  _restoreBounds() {
    const { width: sw, height: sh } = screen.getPrimaryDisplay().workAreaSize;
    const defaults = {
      width:  Math.min(1440, sw),
      height: Math.min(900,  sh),
      x: undefined,
      y: undefined,
    };

    const saved = store.get('bounds');
    if (!saved) return defaults;

    // Verify the saved position is on a currently-connected display
    const visible = screen.getAllDisplays().some((d) => {
      const b = d.workArea;
      return saved.x >= b.x && saved.y >= b.y &&
             saved.x + saved.width  <= b.x + b.width &&
             saved.y + saved.height <= b.y + b.height;
    });

    return visible ? saved : defaults;
  }

  // ── HMI reconnect helpers ──────────────────────────────────────────────────

  _showConnectingPage() {
    if (!this._main || this._main.isDestroyed()) return;
    this._main.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(`
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    * { margin:0; padding:0; box-sizing:border-box; }
    body {
      background:#0F1923; color:#E0E6ED;
      font-family:'Segoe UI',system-ui,sans-serif;
      display:flex; align-items:center; justify-content:center;
      height:100vh;
    }
    .card { text-align:center; max-width:400px; }
    h2 { font-size:22px; margin-bottom:12px; }
    p  { color:#4A5568; font-size:14px; line-height:1.6; }
    .dot { display:inline-block; width:8px; height:8px; border-radius:50%;
           background:#00B4D8; margin:16px 4px 0;
           animation:pulse 1.4s ease-in-out infinite; }
    .dot:nth-child(2){ animation-delay:.2s; }
    .dot:nth-child(3){ animation-delay:.4s; }
    @keyframes pulse{ 0%,100%{opacity:1} 50%{opacity:.2} }
  </style>
</head>
<body>
  <div class="card">
    <h2>Connecting to DWS…</h2>
    <p>The HMI service is starting.<br>This window will reload automatically.</p>
    <div class="dot"></div><div class="dot"></div><div class="dot"></div>
  </div>
</body>
</html>
    `)}`).catch(() => {});
  }

  _showStartupFailedPage() {
    if (!this._main || this._main.isDestroyed()) return;
    this._main.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(`
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    * { margin:0; padding:0; box-sizing:border-box; }
    body {
      background:#0F1923; color:#E0E6ED;
      font-family:'Segoe UI',system-ui,sans-serif;
      display:flex; align-items:center; justify-content:center;
      height:100vh;
    }
    .card { text-align:left; max-width:440px; }
    h2 { font-size:20px; margin-bottom:14px; color:#FF6B6B; }
    p  { color:#9AA5B1; font-size:14px; line-height:1.6; margin-bottom:14px; }
    ul { list-style:none; margin-bottom:18px; }
    li { font-size:14px; color:#CBD5E0; padding:4px 0; }
    li::before { content:"\\2713  "; color:#00B4D8; }
    a {
      display:inline-block; color:#0F1923; background:#00B4D8;
      padding:8px 18px; border-radius:6px; text-decoration:none;
      font-size:14px; font-weight:600;
    }
  </style>
</head>
<body>
  <div class="card">
    <h2>Backend failed to start</h2>
    <p>The HMI service did not respond. Check:</p>
    <ul>
      <li>Python 3 installed and on PATH</li>
      <li>Backend API process running (check Tray → View Logs)</li>
      <li>ROS2 environment sourced, if using colcon build</li>
      <li>RealSense camera connected (USB 3.x)</li>
    </ul>
    <a href="${HMI_URL}">Retry</a>
  </div>
</body>
</html>
    `)}`).catch(() => {});
  }

  _startPollReload() {
    if (this._reloadTimer) return;
    const startedAt = Date.now();
    const TIMEOUT_MS = 30_000;

    this._reloadTimer = setInterval(async () => {
      if (!this._main || this._main.isDestroyed()) {
        clearInterval(this._reloadTimer);
        this._reloadTimer = null;
        return;
      }
      try {
        const { default: fetch } = await import('node-fetch');
        const res = await fetch(HMI_URL, { signal: AbortSignal.timeout(1500) });
        if (res.ok) {
          clearInterval(this._reloadTimer);
          this._reloadTimer = null;
          this._main.loadURL(HMI_URL).catch(() => {});
          return;
        }
      } catch (_) { /* still waiting */ }

      if (Date.now() - startedAt > TIMEOUT_MS) {
        clearInterval(this._reloadTimer);
        this._reloadTimer = null;
        this._showStartupFailedPage();
      }
    }, 2000);
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  _closeSplash() {
    if (this._splash && !this._splash.isDestroyed()) {
      this._splash.close();
      this._splash = null;
    }
  }

  _flushQueue() {
    const win = this.getMainWindow();
    if (!win) return;
    for (const { channel, payload } of this._queue) {
      win.webContents.send(channel, payload);
    }
    this._queue = [];
  }
}

module.exports = WindowManager;