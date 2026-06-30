/**
 * DWS Live Scan Module
 * Drives the operator measurement console:
 *   - WebSocket telemetry (ws://<host>:8765) with auto-reconnect + offline state
 *   - MJPEG depth-vision preview (http://<host>:8090/stream.mjpg)
 *   - FSM pipeline visualisation (idle -> detect -> measure -> weigh -> scan -> lock -> complete)
 *   - Live L/W/H/weight/volume/dim-weight readouts
 *   - Start / Stop / Tare controls (REST, with graceful failure toasts)
 *   - Recent-lock rail sourced from /transactions
 *
 * Does NOT change any backend API contracts or the WebSocket payload schema —
 * it reads whatever fields the backend already broadcasts and degrades
 * gracefully when a field or the whole connection is absent.
 * Build 005.1 — Sprint 3
 */
'use strict';

const DWSLive = (() => {
  const API   = () => `http://${window.location.hostname}:8000`;
  const WS    = () => `ws://${window.location.hostname}:8765`;
  const MJPEG = () => `http://${window.location.hostname}:8090/stream.mjpg`;

  const STATES = ['idle', 'detect', 'measure', 'weigh', 'scan', 'lock', 'complete'];
  let ws = null;
  let reconnectTimer = null;
  let reconnectDelay = 1000;
  let sessionScans = 0;
  let lastValues = {};
  let recentLocks = [];
  let pollTimer = null;

  function init() {
    _attachFeed();
    _connect();
    _bindControls();
    _setState('idle');
    _loadCalibration();
    _loadRecent();
    pollTimer = setInterval(_loadCalibration, 15000);
  }

  function destroy() {
    if (ws) { try { ws.onclose = null; ws.close(); } catch {} ws = null; }
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (pollTimer) clearInterval(pollTimer);
    _detachFeed();
  }

  /* ──────────────── WEBSOCKET ──────────────── */
  function _connect() {
    _setConn('connecting');
    try {
      ws = new WebSocket(WS());
    } catch {
      _setConn('offline');
      _scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      _setConn('live');
      reconnectDelay = 1000;
    };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      _handleMessage(msg);
    };
    ws.onerror = () => { _setConn('offline'); };
    ws.onclose = () => {
      _setConn('offline');
      _scheduleReconnect();
    };
  }

  function _scheduleReconnect() {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => {
      reconnectDelay = Math.min(reconnectDelay * 1.6, 10000);
      _connect();
    }, reconnectDelay);
  }

  function _handleMessage(msg) {
    // Tolerant of several broadcast shapes used by hmi_bridge_node.
    const data = msg.data || msg.payload || msg;

    // State machine
    const state = (data.state || data.fsm_state || msg.state || '').toString().toLowerCase();
    if (state) _setState(state);

    // Measurement block (multiple possible field names)
    const m = data.measurement || data.measurements || data;
    _setMetric('v-length', m.length_cm ?? m.length, 1);
    _setMetric('v-width',  m.width_cm  ?? m.width,  1);
    _setMetric('v-height', m.height_cm ?? m.height, 1);
    _setMetric('v-weight', m.weight_kg ?? m.weight, 2);

    const volCm3 = m.volume_cm3 ?? m.volume;
    if (volCm3 != null && !isNaN(parseFloat(volCm3))) {
      _setMetricRaw('v-volume', (parseFloat(volCm3) / 1000).toFixed(2));
      _setMetricRaw('v-dimweight', (parseFloat(volCm3) / 5000).toFixed(2));
    }

    // Barcode / AWB
    const bc = data.barcode ?? data.awb ?? m.barcode;
    if (bc) _setAwb(bc, data.barcode_status || 'ok');

    // Confidence
    if (m.confidence != null) {
      const c = document.getElementById('kv-confidence');
      if (c) c.textContent = (parseFloat(m.confidence) * (parseFloat(m.confidence) <= 1 ? 100 : 1)).toFixed(1) + '%';
    }

    // Lock event -> record + flash
    if (state === 'lock' || state === 'complete' || data.locked || msg.type === 'lock') {
      _onLock(data, m, bc);
    }
  }

  /* ──────────────── FSM ──────────────── */
  function _setState(state) {
    state = STATES.includes(state) ? state : (state === 'done' || state === 'success' ? 'complete' : state);
    const idx = STATES.indexOf(state);
    document.querySelectorAll('#fsm-pipeline .fsm-state').forEach(el => {
      const s = el.getAttribute('data-state');
      const i = STATES.indexOf(s);
      el.classList.remove('active', 'done', 'complete');
      if (idx < 0) return;
      if (s === 'complete' && state === 'complete') el.classList.add('complete');
      else if (i < idx) el.classList.add('done');
      else if (i === idx) el.classList.add(state === 'complete' ? 'complete' : 'active');
    });

    const kv = document.getElementById('kv-state');
    if (kv) kv.textContent = (state || 'idle').toUpperCase();

    const badge = document.getElementById('live-state-badge');
    if (badge) {
      const map = { idle: 'ready', detect: 'processing', measure: 'processing', weigh: 'processing', scan: 'processing', lock: 'online', complete: 'online' };
      badge.innerHTML = _badge(map[state] || 'offline');
    }

    const vp = document.getElementById('live-viewport');
    if (vp) vp.classList.toggle('scanning', ['detect', 'measure', 'weigh', 'scan'].includes(state));
  }

  function _setMetric(id, val, decimals) {
    if (val == null || isNaN(parseFloat(val))) return;
    _setMetricRaw(id, parseFloat(val).toFixed(decimals));
  }
  function _setMetricRaw(id, text) {
    const el = document.getElementById(id);
    if (!el) return;
    if (el.textContent !== text) {
      el.textContent = text;
      const cell = el.closest('.live-metric');
      if (cell) { cell.classList.add('flash'); setTimeout(() => cell.classList.remove('flash'), 500); }
    }
  }

  function _setAwb(value, status) {
    const el = document.getElementById('awb-value');
    const badge = document.getElementById('awb-badge');
    if (el) { el.textContent = value; el.classList.remove('empty'); }
    if (badge) badge.innerHTML = _badge(status === 'error' ? 'error' : 'online');
  }

  function _onLock(data, m, bc) {
    sessionScans++;
    const sEl = document.getElementById('kv-session');
    if (sEl) sEl.textContent = sessionScans;
    const ll = document.getElementById('kv-lastlock');
    if (ll) ll.textContent = new Date().toLocaleTimeString();

    recentLocks.unshift({
      id: data.id || data.transaction_id || '—',
      barcode: bc || 'No barcode',
      l: m.length_cm ?? m.length, w: m.width_cm ?? m.width, h: m.height_cm ?? m.height,
    });
    recentLocks = recentLocks.slice(0, 8);
    _renderRecent();

    if (window.ToastManager) ToastManager.success('Parcel locked', `${bc || 'Measurement'} recorded successfully.`);
  }

  /* ──────────────── RECENT RAIL ──────────────── */
  function _renderRecent() {
    const rail = document.getElementById('recent-rail');
    if (!rail) return;
    if (!recentLocks.length) {
      rail.innerHTML = `<div class="empty-state" style="padding:var(--sp-8) var(--sp-4);"><div class="empty-state-sub">No locks yet this session</div></div>`;
      return;
    }
    rail.innerHTML = recentLocks.map(r => {
      const l = (parseFloat(r.l) || 0).toFixed(1), w = (parseFloat(r.w) || 0).toFixed(1), h = (parseFloat(r.h) || 0).toFixed(1);
      return `<div class="recent-rail-item">
        <span class="ri-id">#${_esc(String(r.id))}</span>
        <span class="ri-bc">${_esc(r.barcode)}</span>
        <span class="ri-dims">${l}×${w}×${h}</span>
      </div>`;
    }).join('');
  }

  async function _loadRecent() {
    try {
      const r = await fetch(`${API()}/transactions?limit=8`, { signal: AbortSignal.timeout(5000) });
      if (!r.ok) throw new Error('http');
      const d = await r.json();
      const list = Array.isArray(d) ? d : (d.transactions || []);
      recentLocks = list.map(t => ({ id: t.id, barcode: t.barcode || 'No barcode', l: t.length_cm, w: t.width_cm, h: t.height_cm }));
      _renderRecent();
    } catch { /* keep empty state */ }
  }

  /* ──────────────── CALIBRATION STATUS ──────────────── */
  async function _loadCalibration() {
    const el = document.getElementById('kv-cal');
    if (!el) return;
    try {
      const r = await fetch(`${API()}/health`, { signal: AbortSignal.timeout(5000) });
      if (!r.ok) throw new Error('http');
      const d = await r.json();
      const cal = (d.components || {}).calibration;
      const ok = cal === true || cal === 'ok' || cal === 'valid' || cal === 'healthy';
      el.innerHTML = _badge(ok ? 'ready' : cal == null ? 'offline' : 'warning');
      const ct = el.querySelector('.status-badge');
      if (ct) ct.textContent = ok ? 'Valid' : cal == null ? 'Unknown' : 'Check Required';
    } catch {
      el.innerHTML = `<span class="status-badge offline">Unknown</span>`;
    }
  }

  /* ──────────────── CONTROLS ──────────────── */
  function _bindControls() {
    const start = document.getElementById('btn-start');
    const stop  = document.getElementById('btn-stop');
    const tare  = document.getElementById('btn-tare');

    if (start) start.addEventListener('click', () => _command('start', start, stop));
    if (stop)  stop.addEventListener('click', () => _command('stop', start, stop));
    if (tare)  tare.addEventListener('click', () => {
      // Software tare is permanently disabled — legal-metrology constraint.
      // The orchestrator_node.py explicitly forbids software tare.
      if (window.ToastManager) ToastManager.warning('Tare disabled', 'Software tare is not permitted (legal-metrology constraint). Use physical zero controls on the scale hardware.');
    });
  }

  async function _command(action, startBtn, stopBtn) {
    const running = action === 'start';
    if (startBtn) startBtn.disabled = running;
    if (stopBtn)  stopBtn.disabled = !running;
    document.getElementById('pipeline-mode').textContent = running ? 'Running' : 'Manual';

    try {
      const r = await fetch(`${API()}/pipeline/${action}`, { method: 'POST', signal: AbortSignal.timeout(5000) });
      if (!r.ok) throw new Error('http');
      if (window.ToastManager) ToastManager.info(running ? 'Pipeline started' : 'Pipeline stopped', '');
    } catch {
      // The backend may not expose this endpoint; surface honestly without faking data.
      if (window.ToastManager) ToastManager.warning('Command not acknowledged', 'Backend pipeline control is unavailable. Telemetry will still display when the WebSocket connects.');
    }
  }

  async function _tare(btn) {
    btn.classList.add('loading'); btn.disabled = true;
    try {
      const r = await fetch(`${API()}/scale/tare`, { method: 'POST', signal: AbortSignal.timeout(5000) });
      if (!r.ok) throw new Error('http');
      if (window.ToastManager) ToastManager.success('Scale tared', 'Zero reference updated.');
    } catch {
      if (window.ToastManager) ToastManager.warning('Tare unavailable', 'Could not reach the scale node.');
    } finally {
      btn.classList.remove('loading'); btn.disabled = false;
    }
  }

  /* ──────────────── FEED ──────────────── */
  function _attachFeed() {
    const vp = document.getElementById('live-viewport');
    const offline = document.getElementById('live-viewport-offline');
    const liveBadge = document.getElementById('viewport-live-badge');
    const feedBadge = document.getElementById('feed-badge');
    if (!vp) return;

    const img = document.createElement('img');
    img.alt = 'Live depth-vision feed';
    img.style.display = 'none';
    img.addEventListener('load', () => {
      img.style.display = '';
      if (offline) offline.style.display = 'none';
      if (liveBadge) liveBadge.style.display = '';
      if (feedBadge) feedBadge.innerHTML = _badge('online');
    });
    img.addEventListener('error', () => {
      img.style.display = 'none';
      if (offline) offline.style.display = '';
      if (liveBadge) liveBadge.style.display = 'none';
      if (feedBadge) feedBadge.innerHTML = `<span class="status-badge offline">Offline</span>`;
    });
    img.src = MJPEG();
    vp.insertBefore(img, vp.firstChild);
  }

  function _detachFeed() {
    const vp = document.getElementById('live-viewport');
    const img = vp && vp.querySelector('img');
    if (img) img.src = '';
  }

  /* ──────────────── UTIL ──────────────── */
  function _setConn(state) {
    const pill = document.getElementById('ws-conn');
    const text = document.getElementById('ws-conn-text');
    if (!pill) return;
    pill.classList.remove('live', 'offline');
    if (state === 'live')      { pill.classList.add('live');    if (text) text.textContent = 'Live telemetry'; }
    else if (state === 'offline') { pill.classList.add('offline'); if (text) text.textContent = 'Disconnected'; }
    else                       { if (text) text.textContent = 'Connecting…'; }
  }

  function _badge(status) {
    return window.DWS_UI?.makeStatusBadge(status) || `<span class="status-badge ${status}">${status}</span>`;
  }
  function _esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

  return { init, destroy };
})();

window.DWSLive = DWSLive;
if (window.PageRouter) PageRouter.register('live', { init: DWSLive.init, destroy: DWSLive.destroy });
