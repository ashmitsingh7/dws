/**
 * DWS Diagnostics Module
 * Polls /health, /transactions and a system-metrics endpoint, drives the
 * MJPEG live preview, and renders resource gauges + the recent error feed.
 *
 * Endpoints used:
 *   GET  /health                  — existing (see dashboard.js)
 *   GET  /transactions?limit=N    — existing
 *   GET  /system/metrics          — CPU/RAM/disk/network (assumed REST contract,
 *                                   mirrors the shape already used by /health)
 *   MJPEG stream — http://<host>:8090/stream.mjpg (ELECTRON_ARCHITECTURE.md §2: run_dws.py MJPEG :8090)
 *
 * Does NOT change any backend API contracts or the WebSocket protocol.
 * Build 005.1 — Sprint 3
 */
'use strict';

const DWSDiagnostics = (() => {
  const API   = () => `http://${window.location.hostname}:8000`;
  const MJPEG = () => `http://${window.location.hostname}:8090/stream.mjpg`;

  const REFRESH_MS = 5000;
  let refreshTimer = null;
  let countdownTimer = null;
  let countdown = REFRESH_MS / 1000;
  let feedAttached = false;

  const COMPONENTS = [
    { key: 'camera',  icon: 'camera',  title: 'Camera (RealSense D455)' },
    { key: 'scale',   icon: 'scale',   title: 'Weighing Scale' },
    { key: 'barcode', icon: 'barcode', title: 'Barcode Scanner' },
    { key: 'db',      icon: 'storage', title: 'Database (SQLite)' },
    { key: 'api',     icon: 'network', title: 'API Server' },
    { key: 'ros2',    icon: 'cpu',     title: 'ROS2 / SimBus Nodes' },
  ];

  function init() {
    _renderHealthGrid();
    _attachFeed();
    _pollAll();
    refreshTimer = setInterval(_pollAll, REFRESH_MS);
    _startCountdown();
  }

  function destroy() {
    if (refreshTimer) clearInterval(refreshTimer);
    if (countdownTimer) clearInterval(countdownTimer);
    _detachFeed();
  }

  function refreshNow() {
    countdown = REFRESH_MS / 1000;
    _pollAll();
  }

  function _startCountdown() {
    countdownTimer = setInterval(() => {
      countdown = countdown <= 1 ? REFRESH_MS / 1000 : countdown - 1;
      const el = document.getElementById('refresh-countdown');
      if (el) el.textContent = countdown + 's';
    }, 1000);
  }

  function _renderHealthGrid() {
    const grid = document.getElementById('diag-health-grid');
    if (!grid) return;
    grid.innerHTML = COMPONENTS.map(c => ComponentLibrary.healthCard({
      icon: c.icon,
      title: c.title,
      status: 'offline',
      metrics: [{ label: 'Status', value: 'Checking…' }],
    })).join('');
  }

  async function _pollAll() {
    await Promise.allSettled([_fetchHealth(), _fetchMetrics(), _fetchLatestParcel(), _fetchErrors()]);
  }

  async function _fetchHealth() {
    try {
      const r = await fetch(`${API()}/health`, { signal: AbortSignal.timeout(5000) });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      // /health (api_server.py) returns a flat schema, not {components:{...}}.
      // `api` and `ros2` have no backend probe at all — left out here on
      // purpose so _updateHealthGrid's existing fallback (offline/Unknown)
      // keeps showing for those two cards instead of us inventing a value.
      const components = {
        camera:      data.camera?.ok,
        scale:       data.scale?.ok,
        barcode:     undefined, // no backend probe yet
        db:          data.database?.ok,
        disk:        data.disk?.ok,
        calibration: data.calibration?.ok,
      };
      _updateHealthGrid({ ...data, components });
      _updateOverallBadge(data.status || 'offline');
      _updateVisionBadge(components);
    } catch {
      _updateOverallBadge('error');
      COMPONENTS.forEach(c => _setCardStatus(c.key, 'offline'));
    }
  }

  function _updateHealthGrid(health) {
    const components = health.components || {};
    COMPONENTS.forEach(c => {
      const ok = components[c.key];
      let status;
      if (health.stale) status = 'warning';
      else if (ok === true) status = 'online';
      else if (ok === false) status = 'error';
      else status = 'offline'; // no probe for this component yet (e.g. barcode, api, ros2)

      let detail;
      switch (c.key) {
        case 'camera':      detail = health.camera?.reason || (ok ? 'Operational' : 'Offline'); break;
        case 'scale':       detail = health.scale?.reason || (ok ? 'Operational' : 'Offline'); break;
        case 'db':          detail = health.database?.error || (ok ? 'Operational' : 'Offline'); break;
        case 'disk':        detail = health.disk?.reason || (ok ? 'Operational' : 'Offline'); break;
        case 'calibration': detail = health.calibration?.reason || (ok ? 'Operational' : 'Offline'); break;
        case 'barcode':     detail = 'Not monitored'; break; // no backend probe yet
        default:            detail = ok ? 'Operational' : 'Unknown'; // api, ros2 — no probe yet
      }

      _setCardStatus(c.key, status, detail);
    });
  }

  function _setCardStatus(key, status, detail) {
    const idx = COMPONENTS.findIndex(c => c.key === key);
    const card = document.querySelectorAll('#diag-health-grid .dws-health-card')[idx];
    if (!card) return;
    const badgeSlot = card.querySelector('.form-section-header .status-badge')?.parentElement;
    const header = card.querySelector('.form-section-header');
    const oldBadge = header.querySelector('.status-badge');
    if (oldBadge) oldBadge.outerHTML = ComponentLibrary.makeStatusBadge(status);
    const valEl = card.querySelector('.form-section-body .mono');
    if (valEl) valEl.textContent = typeof detail === 'string' ? detail : (status === 'online' ? 'Operational' : status === 'error' ? 'Fault detected' : 'Unknown');
  }

  function _updateOverallBadge(status) {
    const el = document.getElementById('overall-health-badge');
    if (!el) return;
    el.innerHTML = ComponentLibrary.makeStatusBadge(
      status === 'healthy' ? 'online' : status === 'degraded' ? 'warning' : 'error'
    );
  }

  function _updateVisionBadge(components) {
    const el = document.getElementById('vision-status-badge');
    if (!el) return;
    const camOk = components.camera === true || components.camera === 'ok' || components.camera === 'healthy';
    el.innerHTML = ComponentLibrary.makeStatusBadge(camOk ? 'ready' : 'offline');
  }

  async function _fetchMetrics() {
    try {
      const r = await fetch(`${API()}/system/metrics`, { signal: AbortSignal.timeout(5000) });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      _renderGauge('gauge-cpu',     d.cpu_pct ?? 0,  'cpu-detail',     `${(d.cpu_cores ?? '—')} cores`);
      _renderGauge('gauge-ram',     d.ram_pct ?? 0,  'ram-detail',     `${(d.ram_used_gb ?? '—')} / ${(d.ram_total_gb ?? '—')} GB`);
      _renderGauge('gauge-disk',    d.disk_pct ?? 0, 'disk-detail',    `${(d.disk_free_gb ?? '—')} GB free`);
      _renderGauge('gauge-network', d.net_pct ?? 0,  'network-detail',`${(d.net_latency_ms ?? '—')} ms latency`);
    } catch {
      ['gauge-cpu', 'gauge-ram', 'gauge-disk', 'gauge-network'].forEach(id => ChartManager.gauge(id, 0, { label: '%', color: '#4d607d' }));
      ['cpu-detail', 'ram-detail', 'disk-detail', 'network-detail'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = 'Unavailable';
      });
    }
  }

  function _renderGauge(canvasId, pct, detailId, detailText) {
    ChartManager.gauge(canvasId, pct, { label: '%' });
    const el = document.getElementById(detailId);
    if (el && detailText) el.textContent = detailText;
  }

  async function _fetchLatestParcel() {
    try {
      const r = await fetch(`${API()}/transactions?limit=1`, { signal: AbortSignal.timeout(5000) });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      const list = Array.isArray(d) ? d : (d.transactions || []);
      if (!list.length) return;
      _renderLatestParcel(list[0]);
    } catch { /* keep placeholder */ }
  }

  function _renderLatestParcel(tx) {
    const body = document.getElementById('latest-parcel-body');
    const badge = document.getElementById('latest-parcel-badge');
    if (badge) badge.innerHTML = ComponentLibrary.makeStatusBadge(tx.status === 'error' ? 'error' : tx.status === 'warning' ? 'warning' : 'online');
    if (!body) return;
    const l = (tx.length_cm || 0).toFixed(1), w = (tx.width_cm || 0).toFixed(1), h = (tx.height_cm || 0).toFixed(1);
    const wt = parseFloat(tx.weight_kg || 0).toFixed(2);
    const ts = tx.timestamp ? new Date(tx.timestamp).toLocaleTimeString() : '—';
    body.innerHTML = `
      <div class="parcel-measurements" style="margin-bottom:var(--sp-3);">
        <div class="measurement-block"><div class="m-label">L×W×H</div><div class="m-value" style="font-size:var(--text-base);">${l}×${w}×${h}<span class="m-unit">cm</span></div></div>
        <div class="measurement-block"><div class="m-label">Weight</div><div class="m-value" style="font-size:var(--text-base);">${wt}<span class="m-unit">kg</span></div></div>
        <div class="measurement-block"><div class="m-label">Time</div><div class="m-value" style="font-size:var(--text-base);">${ts}</div></div>
      </div>
      <div class="barcode-display">
        <span class="bc-icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-barcode"/></svg></span>
        <span class="bc-value">${_esc(tx.barcode || 'No barcode read')}</span>
      </div>
    `;
  }

  async function _fetchErrors() {
    try {
      const r = await fetch(`${API()}/transactions?limit=50`, { signal: AbortSignal.timeout(5000) });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      const list = Array.isArray(d) ? d : (d.transactions || []);
      const errors = list.filter(t => t.status === 'error' || t.status === 'warning' || t.status === 'failed').slice(0, 10);
      _renderErrors(errors);
      const countEl = document.getElementById('error-count');
      if (countEl) countEl.textContent = `${errors.length} in last batch`;
    } catch {
      const list = document.getElementById('recent-errors-list');
      if (list) list.innerHTML = `<div class="error-row"><span class="error-msg text-tertiary" style="text-align:center;width:100%;padding:24px 0;">Could not load error feed — backend may be offline</span></div>`;
    }
  }

  function _renderErrors(errors) {
    const list = document.getElementById('recent-errors-list');
    if (!list) return;
    if (!errors.length) {
      list.innerHTML = `<div class="error-row"><span class="error-msg text-tertiary" style="text-align:center;width:100%;padding:24px 0;">No errors recorded — system nominal</span></div>`;
      return;
    }
    list.innerHTML = errors.map(e => {
      const sev = e.status === 'error' ? 'critical' : e.status === 'failed' ? 'critical' : 'warning';
      const ts = e.timestamp ? new Date(e.timestamp).toLocaleString() : '—';
      const msg = e.error_message || `${e.status === 'error' ? 'Scan failed' : 'Near out-of-spec measurement'} — barcode ${e.barcode || 'unknown'}`;
      return `
        <div class="error-row severity-${sev}">
          <span class="error-dot"></span>
          <span class="error-msg">${_esc(msg)}</span>
          <span class="error-ts">${ts}</span>
        </div>
      `;
    }).join('');
  }

  function _attachFeed() {
    if (feedAttached) return;
    const viewport = document.getElementById('diag-feed-viewport');
    const offline  = document.getElementById('diag-feed-offline');
    const liveBadge = document.getElementById('feed-live-badge');
    if (!viewport) return;

    const img = document.createElement('img');
    img.alt = 'Live camera feed';
    img.style.display = 'none';
    img.addEventListener('load', () => {
      img.style.display = '';
      if (offline) offline.style.display = 'none';
      if (liveBadge) liveBadge.style.display = '';
      document.getElementById('feed-resolution').textContent = `${img.naturalWidth}×${img.naturalHeight}`;
    });
    img.addEventListener('error', () => {
      img.style.display = 'none';
      if (offline) offline.style.display = '';
      if (liveBadge) liveBadge.style.display = 'none';
    });
    img.src = MJPEG();
    viewport.appendChild(img);
    feedAttached = true;

    // FPS / depth-range are not part of the MJPEG stream metadata; surface
    // placeholders until a future telemetry endpoint exposes them.
    document.getElementById('feed-fps').textContent = '~15';
    document.getElementById('feed-depth').textContent = '0.3–3.0 m';
  }

  function _detachFeed() {
    const viewport = document.getElementById('diag-feed-viewport');
    if (viewport) {
      const img = viewport.querySelector('img');
      if (img) img.src = '';
    }
    feedAttached = false;
  }

  function _esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

  return { init, destroy, refreshNow };
})();

window.DWSDiagnostics = DWSDiagnostics;
PageRouter.register('diagnostics', { init: DWSDiagnostics.init, destroy: DWSDiagnostics.destroy });