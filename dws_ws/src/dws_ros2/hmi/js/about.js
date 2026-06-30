/**
 * DWS About / System Info
 * Renders the version banner, validated software stack, machine info and
 * storage usage. Dynamic values come from Electron (window.dws) and the
 * backend (/system/version, /system/metrics) when available; otherwise the
 * documented build-time defaults are shown.
 *
 * Backend integration note: a dedicated GET /system/version endpoint would
 * let the stack versions below be authoritative instead of build constants.
 * Build 005.1 — Sprint 4
 */
'use strict';

const DWSAbout = (() => {
  const API = () => `http://${window.location.hostname}:8000`;

  // Validated stack (per ARCHITECTURE.md / Productization Analysis).
  // Treated as defaults; overridden by /system/version when present.
  const STACK = [
    { key: 'system', label: 'System Version', value: 'DWS v1.0' },
    { key: 'build', label: 'Build Number', value: '005.1' },
    { key: 'electron', label: 'Electron', value: '—' },
    { key: 'python', label: 'Python', value: '3.10' },
    { key: 'ros2', label: 'ROS2', value: 'Jazzy (SimBus runtime)' },
    { key: 'realsense', label: 'Intel RealSense SDK', value: '2.x' },
    { key: 'opencv', label: 'OpenCV', value: '4.x' },
    { key: 'torch', label: 'Torch', value: '2.8' },
    { key: 'sqlite', label: 'SQLite', value: '3.x' },
  ];

  function init() {
    _renderStack(STACK);
    _renderClientMachine();
    _loadElectron();
    _loadVersion();
    _loadMetrics();
  }

  function _renderStack(stack) {
    const el = document.getElementById('stack-list');
    if (!el) return;
    el.innerHTML = stack.map(s =>
      `<div class="kv-row"><span class="kv-key">${_esc(s.label)}</span><span class="kv-val mono" data-stack="${s.key}">${_esc(s.value)}</span></div>`
    ).join('');
    const ver = stack.find(s => s.key === 'system');
    const build = stack.find(s => s.key === 'build');
    if (ver) document.getElementById('ab-version').textContent = ver.value;
    if (build) document.getElementById('ab-build').textContent = build.value;
  }

  function _setStack(key, value) {
    if (value == null || value === '') return;
    const cell = document.querySelector(`[data-stack="${key}"]`);
    if (cell) cell.textContent = value;
    if (key === 'system') document.getElementById('ab-version').textContent = value;
    if (key === 'build') document.getElementById('ab-build').textContent = value;
  }

  /* ── ELECTRON (window.dws) ───────────────────────────────── */
  async function _loadElectron() {
    const dws = window.dws;
    if (!dws) return;
    try {
      const v = typeof dws.getVersion === 'function' ? await dws.getVersion()
        : typeof dws.appVersion === 'function' ? await dws.appVersion()
        : dws.version || null;
      if (v) {
        _setStack('electron', v.electron || v.electronVersion || v.chrome || '—');
        if (v.app) _setStack('system', `DWS v${String(v.app).replace(/^v/, '')}`);
        if (v.build) _setStack('build', v.build);
      }
    } catch { /* not in Electron or method differs */ }
  }

  /* ── BACKEND VERSION ─────────────────────────────────────── */
  async function _loadVersion() {
    const d = await _tryJson(`${API()}/system/version`);
    if (!d) return;
    const map = {
      system: d.system || d.version, build: d.build || d.build_number,
      electron: d.electron, python: d.python, ros2: d.ros2,
      realsense: d.realsense || d.librealsense, opencv: d.opencv,
      torch: d.torch, sqlite: d.sqlite,
    };
    Object.entries(map).forEach(([k, v]) => _setStack(k, v));
  }

  /* ── MACHINE (client + backend) ──────────────────────────── */
  function _renderClientMachine() {
    const ua = navigator.userAgent || '—';
    const plat = navigator.userAgentData?.platform || navigator.platform || '—';
    _text('m-platform', plat);
    _text('m-ua', ua);
    if (navigator.deviceMemory) _text('m-mem', `${navigator.deviceMemory} GB (approx)`);
    if (navigator.hardwareConcurrency) _text('m-cpu', `${navigator.hardwareConcurrency} logical cores`);
    _text('m-host', window.location.hostname || 'localhost');
  }

  async function _loadMetrics() {
    const d = await _tryJson(`${API()}/system/metrics`);
    if (!d) return;
    // Machine
    if (d.cpu_model || d.cpu_cores) _text('m-cpu', `${d.cpu_model || ''} ${d.cpu_cores ? `(${d.cpu_cores} cores)` : ''}`.trim());
    if (d.ram_total_gb) _text('m-mem', `${d.ram_used_gb ?? '—'} / ${d.ram_total_gb} GB`);
    if (d.platform || d.os) _text('m-platform', d.platform || d.os);
    if (d.hostname) _text('m-host', d.hostname);
    // Storage
    const used = d.disk_used_gb, total = d.disk_total_gb, free = d.disk_free_gb, pct = d.disk_pct;
    if (total != null) {
      const usedGb = used != null ? used : (free != null ? +(total - free).toFixed(1) : null);
      const ratio = pct != null ? pct : (usedGb != null ? (usedGb / total) * 100 : 0);
      const fill = document.getElementById('storage-fill');
      if (fill) {
        fill.style.width = `${Math.min(100, Math.max(0, ratio)).toFixed(0)}%`;
        fill.classList.toggle('warn', ratio >= 75 && ratio < 90);
        fill.classList.toggle('crit', ratio >= 90);
      }
      _text('storage-used', usedGb != null ? `${usedGb} GB used` : `${ratio.toFixed(0)}% used`);
      _text('storage-free', free != null ? `${free} GB free` : `of ${total} GB`);
    }
    if (d.db_size_mb != null) _text('s-db', `${d.db_size_mb} MB`);
    if (d.captures_mb != null) _text('s-captures', `${d.captures_mb} MB`);
    if (d.pointclouds_mb != null) _text('s-pcl', `${d.pointclouds_mb} MB`);
  }

  /* ── HELPERS ─────────────────────────────────────────────── */
  async function _tryJson(url) {
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(5000) });
      if (!r.ok) return null;
      return await r.json();
    } catch { return null; }
  }
  function _text(id, v) { const el = document.getElementById(id); if (el && v != null && v !== '') el.textContent = v; }
  function _esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

  return { init };
})();

window.DWSAbout = DWSAbout;
