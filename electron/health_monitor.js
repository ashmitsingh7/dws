'use strict';

/**
 * DWS Desktop — Health Monitor  (Sprint 2 — production)
 *
 * Polls DWS backend health every 5 s via HTTP and every 15 s via WebSocket.
 * Maps the FastAPI /health JSON payload to a typed HealthSnapshot and emits
 * it via the 'health' event and the onHealth callback.
 *
 * Backend /health payload keys (from health_monitor.py):
 *   camera_status       'ok' | 'error' | 'missing'
 *   db_status           'ok' | 'error'
 *   disk_ok             true | false
 *   calibration_status  'ok' | 'missing' | 'stale'
 *   scale_status        'ok' | 'error' | 'missing'
 *   ws_clients          number
 */

const EventEmitter = require('events');
const WebSocket    = require('ws');

const HTTP_INTERVAL_MS = 5_000;
const WS_INTERVAL_MS   = 15_000;
const FETCH_TIMEOUT_MS = 3_000;
const WS_TIMEOUT_MS    = 4_000;

class HealthMonitor extends EventEmitter {
  constructor({ apiBase, wsUrl, mjpegUrl, log, onHealth }) {
    super();

    this._api    = apiBase;
    this._wsUrl  = wsUrl;
    this._mjpeg  = mjpegUrl;
    this._log    = log || console;

    if (onHealth) this.on('health', onHealth);

    /** @type {HealthSnapshot|null} */
    this._latest  = null;
    this._wsAlive = false;

    this._httpTimer = null;
    this._wsTimer   = null;

    // Track consecutive failures to avoid log spam
    this._httpFails = 0;
    this._wsFails   = 0;
  }

  start() {
    if (this._httpTimer) return;

    this._pollHttp();
    this._probeWs();

    this._httpTimer = setInterval(() => this._pollHttp(), HTTP_INTERVAL_MS);
    this._wsTimer   = setInterval(() => this._probeWs(),  WS_INTERVAL_MS);

    this._log.info('Health monitoring started');
  }

  stop() {
    clearInterval(this._httpTimer);
    clearInterval(this._wsTimer);
    this._httpTimer = null;
    this._wsTimer   = null;
    this._log.info('Health monitoring stopped');
  }

  /** @returns {HealthSnapshot|null} */
  getLatest() {
    return this._latest;
  }

  // ── HTTP poll ──────────────────────────────────────────────────────────────

  async _pollHttp() {
    let apiOk       = false;
    let camera      = null;
    let db          = null;
    let disk        = null;
    let calibration = null;
    let scale       = null;

    try {
      const { default: fetch } = await import('node-fetch');
      const res = await fetch(
        `${this._api}/health`,
        { signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) }
      );

      if (res.ok) {
        apiOk = true;
        this._httpFails = 0;

        try {
          const body = await res.json();
          camera      = body.camera_status      === 'ok';
          db          = body.db_status          === 'ok';
          disk        = body.disk_ok            === true;
          calibration = body.calibration_status === 'ok';
          scale       = body.scale_status       === 'ok';
        } catch (parseErr) {
          this._log.warn(`Health payload parse error: ${parseErr.message}`);
        }
      } else {
        this._httpFails++;
        if (this._httpFails <= 3 || this._httpFails % 10 === 0) {
          this._log.warn(`Health HTTP ${res.status} (consecutive fails: ${this._httpFails})`);
        }
      }
    } catch (err) {
      this._httpFails++;
      if (this._httpFails <= 3 || this._httpFails % 10 === 0) {
        this._log.debug(`Health HTTP error: ${err.message} (consecutive: ${this._httpFails})`);
      }
    }

    this._publish({ apiOk, camera, db, disk, calibration, scale });
  }

  // ── WebSocket probe ────────────────────────────────────────────────────────

  _probeWs() {
    return new Promise((resolve) => {
      let settled = false;

      const done = (alive) => {
        if (settled) return;
        settled = true;

        if (alive !== this._wsAlive) {
          this._log.info(`WebSocket health: ${alive ? 'up' : 'down'}`);
        }

        if (!alive) {
          this._wsFails++;
          if (this._wsFails <= 3 || this._wsFails % 5 === 0) {
            this._log.warn(`WebSocket :8765 unreachable (consecutive: ${this._wsFails})`);
          }
        } else {
          this._wsFails = 0;
        }

        this._wsAlive = alive;
        this._publishWsUpdate(alive);
        resolve(alive);
      };

      const timer = setTimeout(() => done(false), WS_TIMEOUT_MS);

      let ws;
      try {
        ws = new WebSocket(this._wsUrl);
      } catch (_) {
        clearTimeout(timer);
        done(false);
        return;
      }

      ws.on('open', () => {
        clearTimeout(timer);
        try { ws.close(); } catch (_) {}
        done(true);
      });

      ws.on('error', () => {
        clearTimeout(timer);
        done(false);
      });
    });
  }

  // ── Publish ────────────────────────────────────────────────────────────────

  _publish({ apiOk, camera, db, disk, calibration, scale }) {
    this._latest = {
      api:         apiOk,
      ws:          this._wsAlive,
      camera,
      db,
      disk,
      calibration,
      scale,
      timestamp:   Date.now(),
    };
    this.emit('health', this._latest);
  }

  _publishWsUpdate(alive) {
    if (!this._latest) return;
    this._latest = { ...this._latest, ws: alive, timestamp: Date.now() };
    this.emit('health', this._latest);
  }
}

module.exports = HealthMonitor;
