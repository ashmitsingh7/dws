'use strict';

/**
 * DWS Desktop — System Tray  (Sprint 2 — production)
 *
 * Creates and manages the system tray icon + context menu.
 * Icon state reflects live health: idle → ok → warn → error.
 */

const { Tray, Menu, nativeImage, app } = require('electron');
const path = require('path');
const fs   = require('fs');

const ICONS_DIR = path.join(__dirname, 'assets', 'icons');

const ICON_FILES = {
  ok:    path.join(ICONS_DIR, 'tray-ok.png'),
  warn:  path.join(ICONS_DIR, 'tray-warn.png'),
  error: path.join(ICONS_DIR, 'tray-error.png'),
  idle:  path.join(ICONS_DIR, 'tray-idle.png'),
};

// Fallback 16×16 coloured squares if PNG files are missing (dev mode)
const FALLBACK_COLORS = {
  ok:    '#00B4D8',
  warn:  '#F59E0B',
  error: '#EF4444',
  idle:  '#4A5568',
};

let _handlers = null;
let _lastHealth = null;

function createTray({ onOpen, onRestart, onLogs, onShutdown, onExit }) {
  _handlers = { onOpen, onRestart, onLogs, onShutdown, onExit };

  const tray = new Tray(_resolveIcon('idle'));
  tray.setToolTip('DWS — Dimensional Weighing System\nStarting…');

  _rebuildMenu(tray, false);

  tray.on('double-click', onOpen);

  return tray;
}

function updateTray(tray, status) {
  if (!tray || tray.isDestroyed()) return;

  _lastHealth = { ..._lastHealth, ...status };

  const icon = _deriveIcon(_lastHealth);
  const tip  = _deriveTooltip(_lastHealth);

  tray.setImage(_resolveIcon(icon));
  tray.setToolTip(tip);

  const monitorOnly = _lastHealth.monitorOnly === true;
  _rebuildMenu(tray, monitorOnly);
}

function destroyTray(tray) {
  if (tray && !tray.isDestroyed()) tray.destroy();
}

// ── Menu ───────────────────────────────────────────────────────────────────────

function _rebuildMenu(tray, monitorOnly) {
  if (!_handlers) return;
  const { onOpen, onRestart, onLogs, onShutdown, onExit } = _handlers;

  const h = _lastHealth || {};

  const statusItems = [
    { label: `Backend   ${_icon(h.backend)}`,     enabled: false },
    { label: `API       ${_icon(h.api)}`,         enabled: false },
    { label: `WebSocket ${_icon(h.ws)}`,          enabled: false },
    { label: `Camera    ${_icon(h.camera)}`,      enabled: false },
    { label: `Database  ${_icon(h.db)}`,          enabled: false },
    { label: `Disk      ${_icon(h.disk)}`,        enabled: false },
    { label: `Scale     ${_icon(h.scale)}`,       enabled: false },
    { label: `Calibration ${_icon(h.calibration)}`, enabled: false },
  ];

  const template = [
    {
      label: 'Open DWS',
      click: onOpen,
    },
    { type: 'separator' },
    {
      label:   'Restart Services',
      enabled: !monitorOnly,
      click:   onRestart,
    },
    { type: 'separator' },
    {
      label: 'View Logs',
      click: onLogs,
    },
    {
      label:   'Status',
      submenu: statusItems,
    },
    { type: 'separator' },
    {
      label: `DWS v${app.getVersion()}`,
      enabled: false,
    },
    { type: 'separator' },
    {
      label: 'Shutdown',
      click: onShutdown,
    },
    {
      label: 'Exit',
      click: onExit,
    },
  ];

  tray.setContextMenu(Menu.buildFromTemplate(template));
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function _icon(val) {
  if (val === true)  return '●';   // filled circle — ok
  if (val === false) return '○';   // empty circle  — error
  return '·';                      // dot           — unknown
}

function _deriveIcon(h) {
  if (!h || (h.backend === undefined && h.api === undefined)) return 'idle';
  if (h.backend === false || h.api === false) return 'error';
  if (h.ws === false || h.camera === false || h.db === false) return 'warn';
  if (h.backend === true && h.api === true) return 'ok';
  return 'idle';
}

function _deriveTooltip(h) {
  if (!h) return 'DWS — starting…';
  const lines = ['DWS — Dimensional Weighing System'];
  const s = (v) => v === true ? 'ok' : v === false ? 'DOWN' : '…';
  lines.push(`Backend: ${s(h.backend)}  API: ${s(h.api)}  WS: ${s(h.ws)}`);
  lines.push(`Camera: ${s(h.camera)}  DB: ${s(h.db)}  Scale: ${s(h.scale)}`);
  return lines.join('\n');
}

function _resolveIcon(name) {
  const filePath = ICON_FILES[name] || ICON_FILES.idle;
  if (fs.existsSync(filePath)) return filePath;

  // Generate a 16×16 coloured square as fallback
  const color = FALLBACK_COLORS[name] || '#4A5568';
  return _colorIcon(color);
}

function _colorIcon(hex) {
  // 16×16 1-bit BMP would be simpler but nativeImage.createFromBuffer needs PNG.
  // Build a minimal 16×16 PNG programmatically using raw bytes.
  // Easier: use a data URL.
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);

  // 1×1 solid colour PNG — tray will scale it
  const png1x1 = Buffer.from([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,  // PNG signature
    0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,  // IHDR length + type
    0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,  // width=1, height=1
    0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,  // 8bit RGB, CRC
    0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41,  // IDAT length + type
    0x54, 0x08, 0xD7, 0x63, r, g, b, 0x00,           // IDAT (deflate)
    0x00, 0x00, 0x02, 0x00, 0x01, 0xE2, 0x21,        // IDAT end + CRC (approx)
    0xBC, 0x33, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45,  // IEND
    0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
  ]);

  try {
    return nativeImage.createFromBuffer(png1x1);
  } catch (_) {
    return nativeImage.createEmpty();
  }
}

module.exports = { createTray, updateTray, destroyTray };
