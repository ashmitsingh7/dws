'use strict';

/**
 * DWS Desktop — Preload / Context Bridge  (Sprint 2 — production)
 *
 * Exposes window.dws to the HMI renderer.
 * The existing hmi/index.html is NOT modified — window.dws is available for
 * future native overlays, status bars, or auth layers added on top.
 *
 * All channels must match handlers in ipc_handlers.js.
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('dws', {

  // ── Supervisor ─────────────────────────────────────────────────────────────
  startBackend:       () => ipcRenderer.invoke('supervisor:start'),
  stopBackend:        () => ipcRenderer.invoke('supervisor:stop'),
  restartBackend:     () => ipcRenderer.invoke('supervisor:restart'),
  getSupervisorStatus:() => ipcRenderer.invoke('supervisor:status'),

  // ── Health ─────────────────────────────────────────────────────────────────
  getHealth: () => ipcRenderer.invoke('health:get'),

  // ── App ───────────────────────────────────────────────────────────────────
  getVersion: () => ipcRenderer.invoke('app:version'),
  getLogsDir: () => ipcRenderer.invoke('app:logs-dir'),
  openLogs:   () => ipcRenderer.invoke('app:open-logs'),
  quit:       () => ipcRenderer.invoke('app:quit'),
  setKiosk:   (enabled) => ipcRenderer.invoke('app:set-kiosk', enabled),
  getKiosk:   () => ipcRenderer.invoke('app:get-kiosk'),

  // ── Window ────────────────────────────────────────────────────────────────
  minimize:       () => ipcRenderer.invoke('window:minimize'),
  maximizeToggle: () => ipcRenderer.invoke('window:maximize-toggle'),
  hideToTray:     () => ipcRenderer.invoke('window:close'),

  // ── Events (renderer subscribes, returns unsubscribe fn) ─────────────────

  /**
   * Subscribe to backend process state changes.
   * @param {(status: ProcessStatus) => void} cb
   * @returns {() => void} unsubscribe
   */
  onSupervisorStatus(cb) {
    const h = (_e, v) => cb(v);
    ipcRenderer.on('supervisor:status', h);
    return () => ipcRenderer.removeListener('supervisor:status', h);
  },

  /**
   * Subscribe to health snapshots (every 5 s).
   * @param {(health: HealthSnapshot) => void} cb
   * @returns {() => void} unsubscribe
   */
  onHealthUpdate(cb) {
    const h = (_e, v) => cb(v);
    ipcRenderer.on('health:update', h);
    return () => ipcRenderer.removeListener('health:update', h);
  },

  /**
   * Subscribe to startup progress messages shown during boot.
   * @param {(p: {step:number, total:number, message:string}) => void} cb
   * @returns {() => void} unsubscribe
   */
  onStartupProgress(cb) {
    const h = (_e, v) => cb(v);
    ipcRenderer.on('startup:progress', h);
    return () => ipcRenderer.removeListener('startup:progress', h);
  },
});

/*
 * Types (JSDoc — informational only):
 *
 * @typedef {Object} ProcessStatus
 * @property {boolean} backend
 * @property {boolean} api
 * @property {boolean} hmi
 * @property {boolean} monitorOnly
 * @property {{ backend?:number, api?:number, hmi?:number }} pids
 *
 * @typedef {Object} HealthSnapshot
 * @property {boolean|null} api
 * @property {boolean|null} ws
 * @property {boolean|null} camera
 * @property {boolean|null} db
 * @property {boolean|null} disk
 * @property {boolean|null} calibration
 * @property {boolean|null} scale
 * @property {number} timestamp
 */
