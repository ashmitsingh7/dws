/**
 * DWS Dashboard Module
 * Polls /health and /transactions for KPIs, renders mini throughput chart,
 * updates live status panel.
 * Build 005.1 — Sprint 2
 *
 * Does NOT change any backend API contracts.
 */
'use strict';

const DWSDashboard = (() => {
  const API = () => `http://${window.location.hostname}:8000`;
  let pollInterval = null;
  let chartData    = [];

  /* ──────────────── KPI POLLING ──────────────── */
  async function init() {
    await _pollAll();
    pollInterval = setInterval(_pollAll, 10000);
  }

  function destroy() {
    if (pollInterval) clearInterval(pollInterval);
  }

  async function _pollAll() {
    await Promise.allSettled([_fetchHealth(), _fetchStats()]);
  }

  async function _fetchHealth() {
    try {
      const r = await fetch(`${API()}/health`, { signal: AbortSignal.timeout(5000) });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      _updateHealthPanel(d);
    } catch (e) {
      _updateHealthPanel({ status: 'error', components: {} });
    }
  }

  async function _fetchStats() {
    try {
      const r = await fetch(`${API()}/transactions?limit=100`, { signal: AbortSignal.timeout(5000) });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      const txList = Array.isArray(d) ? d : (d.transactions || []);
      _updateKPIs(txList);
      _updateMiniChart(txList);
    } catch {}
  }

  function _updateHealthPanel(health) {
    // /health (api_server.py) returns a flat schema — {camera:{ok,reason},
    // scale:{ok,reason}, disk:{ok,...}, calibration:{ok,...},
    // database:{ok,...}} — not the {components:{...}} shape this used to
    // read. There is also no `barcode` probe on the backend at all yet,
    // so that card is always reported as unknown/offline until a real
    // backend check exists for it — this is not a frontend bug to paper
    // over with a fake "online".
    const components = {
      camera:  health.camera?.ok,
      scale:   health.scale?.ok,
      barcode: undefined, // no backend probe yet — always falls through below
      db:      health.database?.ok,
      disk:    health.disk?.ok,
    };

    const componentMap = {
      camera:  { id: 'health-camera', label: 'Camera' },
      scale:   { id: 'health-scale',  label: 'Scale'  },
      barcode: { id: 'health-barcode',label: 'Barcode' },
      db:      { id: 'health-db',     label: 'Database'},
      disk:    { id: 'health-disk',   label: 'Storage' },
    };

    Object.entries(componentMap).forEach(([key, cfg]) => {
      const el = document.getElementById(cfg.id);
      if (!el) return;

      const ok = components[key];
      let status;
      if (health.stale) status = 'warning';
      else if (ok === true) status = 'online';
      else status = 'offline'; // covers ok === false and ok === undefined (no probe)

      el.innerHTML = makeStatusBadge(status);
    });

    // Overall indicator
    const overallEl = document.getElementById('overall-health-badge');
    if (overallEl) {
      const s = health.status || 'offline';
      overallEl.innerHTML = makeStatusBadge(
        s === 'healthy' ? 'online' : s === 'degraded' ? 'warning' : 'error'
      );
    }
  }

  function _updateKPIs(txList) {
    const today = new Date().toISOString().slice(0, 10);
    const todayTx = txList.filter(t => (t.timestamp || '').startsWith(today));

    _setKPI('kpi-total-today',  todayTx.length);
    _setKPI('kpi-total-all',    txList.length);

    if (todayTx.length) {
      const weights = todayTx.map(t => parseFloat(t.weight_kg || 0)).filter(w => w > 0);
      const avgW = weights.length ? (weights.reduce((a, b) => a + b, 0) / weights.length) : 0;
      _setKPI('kpi-avg-weight', avgW.toFixed(2));

      const volumes = todayTx.map(t => parseFloat(t.volume_cm3 || t.volume || 0)).filter(v => v > 0);
      const avgV = volumes.length ? (volumes.reduce((a, b) => a + b, 0) / volumes.length) : 0;
      _setKPI('kpi-avg-volume', (avgV / 1000).toFixed(1)); // to litres
    }

    const errors = txList.filter(t => t.status === 'error' || t.status === 'failed');
    _setKPI('kpi-errors', errors.length);

    // Throughput: tx per hour today
    if (todayTx.length >= 2) {
      const times = todayTx.map(t => new Date(t.timestamp).getTime()).filter(Boolean);
      const span  = (Math.max(...times) - Math.min(...times)) / 3600000; // hours
      const rate  = span > 0 ? (todayTx.length / span).toFixed(1) : todayTx.length;
      _setKPI('kpi-throughput', rate);
    }
  }

  function _setKPI(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    if (window.animateNumber) {
      animateNumber(el, parseFloat(value) || 0, { decimals: String(value).includes('.') ? 1 : 0 });
    } else {
      el.textContent = value;
    }
  }

  function _updateMiniChart(txList) {
    const canvas = document.getElementById('throughput-canvas');
    if (!canvas) return;

    // Build hourly buckets for last 12 hours
    const now    = Date.now();
    const hours  = 12;
    const buckets = Array.from({ length: hours }, (_, i) => ({
      label: `${((new Date().getHours() - (hours - 1 - i) + 24) % 24).toString().padStart(2,'0')}:00`,
      count: 0,
    }));

    txList.forEach(t => {
      const ts = new Date(t.timestamp).getTime();
      const h  = Math.floor((now - ts) / 3600000);
      if (h >= 0 && h < hours) buckets[hours - 1 - h].count++;
    });

    _drawBarChart(canvas, buckets);
  }

  function _drawBarChart(canvas, buckets) {
    const ctx  = canvas.getContext('2d');
    const W    = canvas.offsetWidth  || 600;
    const H    = canvas.offsetHeight || 120;
    canvas.width  = W;
    canvas.height = H;

    ctx.clearRect(0, 0, W, H);

    const maxVal = Math.max(1, ...buckets.map(b => b.count));
    const padL = 8, padR = 8, padT = 8, padB = 28;
    const innerW = W - padL - padR;
    const innerH = H - padT - padB;
    const barW   = (innerW / buckets.length) * 0.6;
    const gap    = (innerW / buckets.length) * 0.4;

    // Primary color from CSS var
    const style  = getComputedStyle(document.documentElement);
    const clrPrimary = style.getPropertyValue('--clr-primary').trim() || '#1a8cff';
    const clrText    = style.getPropertyValue('--clr-text-tertiary').trim() || '#4d607d';
    const clrBorder  = style.getPropertyValue('--clr-border-subtle').trim() || '#1a2235';

    // Grid lines
    ctx.strokeStyle = clrBorder;
    ctx.lineWidth = 1;
    [0.25, 0.5, 0.75, 1].forEach(frac => {
      const y = padT + innerH * (1 - frac);
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y);
      ctx.stroke();
    });

    buckets.forEach((b, i) => {
      const x    = padL + i * (barW + gap);
      const bh   = Math.max(2, (b.count / maxVal) * innerH);
      const y    = padT + innerH - bh;

      // Bar fill with gradient
      const grad = ctx.createLinearGradient(0, y, 0, y + bh);
      grad.addColorStop(0, clrPrimary);
      grad.addColorStop(1, clrPrimary + '55');
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.roundRect(x, y, barW, bh, [3, 3, 0, 0]);
      ctx.fill();

      // Label
      if (i % 3 === 0) {
        ctx.fillStyle = clrText;
        ctx.font = `10px var(--font-mono, monospace)`;
        ctx.textAlign = 'center';
        ctx.fillText(b.label, x + barW / 2, H - 6);
      }
    });
  }

  function makeStatusBadge(status) {
    return window.DWS_UI?.makeStatusBadge(status) || `<span class="status-badge ${status}">${status}</span>`;
  }

  return { init, destroy };
})();

window.DWSDashboard = DWSDashboard;