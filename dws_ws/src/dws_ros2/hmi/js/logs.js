/**
 * DWS Audit Logs
 * Searchable, filterable, paginated event log with CSV / JSON export.
 *
 * Endpoints (tried in order, degrades gracefully when offline):
 *   GET /logs?limit=N      — preferred dedicated audit-log endpoint (see
 *                            AUTHENTICATION_ARCHITECTURE.md: auth_login_history)
 *   GET /errors?limit=N    — error table fallback
 *   GET /transactions?limit=N — last-resort synthesis from scan records
 *
 * Build 005.1 — Sprint 4
 */
'use strict';

const DWSLogs = (() => {
  const API = () => `http://${window.location.hostname}:8000`;
  const PAGE_SIZE = 30;

  let all = [];
  let filtered = [];
  let page = 1;

  function init() {
    _bind();
    load();
  }

  function _bind() {
    document.getElementById('btn-refresh-logs')?.addEventListener('click', load);
    document.getElementById('btn-export-csv')?.addEventListener('click', _exportCSV);
    document.getElementById('btn-export-json')?.addEventListener('click', _exportJSON);
    document.getElementById('btn-clear-filters')?.addEventListener('click', _clearFilters);
    ['log-search', 'log-severity', 'log-source', 'log-from', 'log-to'].forEach(id => {
      const el = document.getElementById(id);
      const evt = el && el.tagName === 'INPUT' && el.type !== 'datetime-local' ? 'input' : 'change';
      el?.addEventListener(evt, () => { page = 1; _apply(); });
    });
  }

  async function load() {
    const tbody = document.getElementById('logs-tbody');
    tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;padding:40px;color:var(--clr-text-tertiary);">Loading events…</td></tr>`;
    all = await _fetchLogs();
    _populateSources();
    page = 1;
    _apply();
  }

  async function _fetchLogs() {
    // 1) dedicated audit-log endpoint
    const direct = await _tryJson(`${API()}/logs?limit=1000`);
    if (direct) return _normalize(direct, 'logs');
    // 2) errors table
    const errs = await _tryJson(`${API()}/errors?limit=1000`);
    if (errs) return _normalize(errs, 'errors');
    // 3) synthesize from transactions
    const tx = await _tryJson(`${API()}/transactions?limit=1000`);
    if (tx) return _normalize(tx, 'transactions');
    return null; // offline
  }

  async function _tryJson(url) {
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(5000) });
      if (!r.ok) return null;
      const d = await r.json();
      const list = Array.isArray(d) ? d : (d.logs || d.errors || d.transactions || d.items || []);
      return Array.isArray(list) ? list : null;
    } catch { return null; }
  }

  function _normalize(list, kind) {
    return list.map(row => {
      if (kind === 'transactions') {
        const sev = row.status === 'error' || row.status === 'failed' ? 'error'
          : row.status === 'warning' || row.status === 'near_oos' ? 'warning'
          : 'info';
        const msg = row.error_message
          || (sev === 'info'
            ? `Scan locked — barcode ${row.barcode || 'unknown'}`
            : `${sev === 'error' ? 'Scan failed' : 'Near out-of-spec'} — barcode ${row.barcode || 'unknown'}`);
        return { ts: row.timestamp, severity: sev, source: 'pipeline', message: msg, detail: row };
      }
      // logs / errors: respect explicit fields when present
      const sev = (row.severity || row.level || row.status || 'info').toString().toLowerCase();
      const norm = ['critical', 'error', 'warning', 'info', 'success'].includes(sev) ? sev
        : sev === 'warn' ? 'warning'
        : sev === 'fatal' ? 'critical'
        : sev === 'failed' ? 'error'
        : 'info';
      return {
        ts: row.timestamp || row.time || row.created_at,
        severity: norm,
        source: (row.source || row.component || row.node || row.module || 'system').toString(),
        message: row.message || row.error_message || row.msg || row.event || '—',
        detail: row,
      };
    }).filter(Boolean);
  }

  function _populateSources() {
    const sel = document.getElementById('log-source');
    if (!sel) return;
    const current = sel.value;
    const sources = [...new Set(all.map(l => l.source).filter(Boolean))].sort();
    sel.innerHTML = `<option value="">All</option>` + sources.map(s => `<option value="${_esc(s)}">${_esc(s)}</option>`).join('');
    sel.value = current;
  }

  function _apply() {
    if (all === null) {
      _renderOffline();
      return;
    }
    const q = (document.getElementById('log-search').value || '').trim().toLowerCase();
    const sev = document.getElementById('log-severity').value;
    const src = document.getElementById('log-source').value;
    const fromV = document.getElementById('log-from').value;
    const toV = document.getElementById('log-to').value;
    const fromMs = fromV ? new Date(fromV).getTime() : 0;
    const toMs = toV ? new Date(toV).getTime() : Infinity;

    filtered = all.filter(l => {
      if (sev && l.severity !== sev) return false;
      if (src && l.source !== src) return false;
      const t = new Date(l.ts || 0).getTime();
      if (t < fromMs || t > toMs) return false;
      if (q) {
        const hay = `${l.message} ${l.source} ${l.severity}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    }).sort((a, b) => new Date(b.ts || 0) - new Date(a.ts || 0));

    _renderStats();
    _render();
  }

  function _renderStats() {
    document.getElementById('stat-total').textContent = all.length;
    document.getElementById('stat-critical').textContent = all.filter(l => l.severity === 'critical' || l.severity === 'error').length;
    document.getElementById('stat-warning').textContent = all.filter(l => l.severity === 'warning').length;
    document.getElementById('stat-shown').textContent = filtered.length;
    document.getElementById('log-count').textContent = `${filtered.length} event${filtered.length !== 1 ? 's' : ''}`;
  }

  function _render() {
    const tbody = document.getElementById('logs-tbody');
    const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
    page = Math.min(page, totalPages);
    const start = (page - 1) * PAGE_SIZE;
    const rows = filtered.slice(start, start + PAGE_SIZE);

    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;padding:40px;color:var(--clr-text-tertiary);">No events match your filters</td></tr>`;
    } else {
      tbody.innerHTML = rows.map((l, i) => {
        const ts = l.ts ? new Date(l.ts).toLocaleString() : '—';
        const sev = l.severity;
        const label = sev.charAt(0).toUpperCase() + sev.slice(1);
        const detail = _detailText(l.detail);
        const idx = start + i;
        return `<tr class="log-row" data-idx="${idx}">
          <td class="mono dim">${ts}</td>
          <td><span class="sev-cell sev-${sev}"><span class="sev-dot"></span><span class="sev-label">${label}</span></span></td>
          <td><span class="log-source-pill">${_esc(l.source)}</span></td>
          <td class="log-msg">${_esc(l.message)}</td>
        </tr>${detail ? `<tr class="log-detail-row" data-detail="${idx}" style="display:none;"><td colspan="4">${_esc(detail)}</td></tr>` : ''}`;
      }).join('');

      tbody.querySelectorAll('.log-row').forEach(row => {
        row.addEventListener('click', () => {
          const d = tbody.querySelector(`.log-detail-row[data-detail="${row.dataset.idx}"]`);
          if (d) d.style.display = d.style.display === 'none' ? '' : 'none';
        });
      });
    }
    _renderPagination(totalPages, start);
  }

  function _detailText(detail) {
    if (!detail || typeof detail !== 'object') return '';
    try { return JSON.stringify(detail, null, 2); } catch { return ''; }
  }

  function _renderPagination(totalPages, start) {
    const pag = document.getElementById('logs-pagination');
    if (!pag) return;
    const end = Math.min(start + PAGE_SIZE, filtered.length);
    pag.querySelector('.pagination-info').textContent =
      filtered.length === 0 ? 'No events' : `${start + 1}–${end} of ${filtered.length}`;
    const ctrl = pag.querySelector('.pagination-controls');
    ctrl.innerHTML = '';
    const mk = (label, disabled, p, active) => {
      const b = document.createElement('button');
      b.className = 'pagination-btn' + (active ? ' active' : '');
      b.textContent = label;
      b.disabled = disabled;
      if (!disabled) b.addEventListener('click', () => { page = p; _render(); });
      return b;
    };
    ctrl.appendChild(mk('‹', page <= 1, page - 1));
    let pages = totalPages <= 7 ? Array.from({ length: totalPages }, (_, i) => i + 1) : [1];
    if (totalPages > 7) {
      const lo = Math.max(2, page - 1), hi = Math.min(totalPages - 1, page + 1);
      if (lo > 2) pages.push('…');
      for (let p = lo; p <= hi; p++) pages.push(p);
      if (hi < totalPages - 1) pages.push('…');
      pages.push(totalPages);
    }
    pages.forEach(p => {
      if (p === '…') {
        const s = document.createElement('span');
        s.textContent = '…';
        s.style.cssText = 'padding:0 6px;color:var(--clr-text-tertiary);align-self:center;';
        ctrl.appendChild(s);
      } else ctrl.appendChild(mk(String(p), false, p, p === page));
    });
    ctrl.appendChild(mk('›', page >= totalPages, page + 1));
  }

  function _renderOffline() {
    document.getElementById('logs-tbody').innerHTML =
      `<tr><td colspan="4" style="text-align:center;padding:40px;color:var(--clr-text-tertiary);">Could not load logs — backend may be offline</td></tr>`;
    ['stat-total', 'stat-critical', 'stat-warning', 'stat-shown'].forEach(id => {
      const el = document.getElementById(id); if (el) el.textContent = '—';
    });
    document.getElementById('log-count').textContent = '0 events';
  }

  /* ── EXPORT ──────────────────────────────────────────────── */
  function _exportCSV() {
    if (!filtered.length) return;
    const header = 'Timestamp,Severity,Source,Message';
    const body = filtered.map(l =>
      [l.ts || '', l.severity, l.source, l.message].map(_csv).join(',')
    ).join('\n');
    _download(`dws-logs-${_stamp()}.csv`, header + '\n' + body, 'text/csv');
  }

  function _exportJSON() {
    if (!filtered.length) return;
    const payload = filtered.map(({ ts, severity, source, message }) => ({ ts, severity, source, message }));
    _download(`dws-logs-${_stamp()}.json`, JSON.stringify(payload, null, 2), 'application/json');
  }

  function _clearFilters() {
    document.getElementById('log-search').value = '';
    document.getElementById('log-severity').value = '';
    document.getElementById('log-source').value = '';
    document.getElementById('log-from').value = '';
    document.getElementById('log-to').value = '';
    page = 1;
    _apply();
  }

  /* ── HELPERS ─────────────────────────────────────────────── */
  function _csv(v) {
    const s = String(v ?? '');
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }
  function _download(name, content, type) {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  }
  function _stamp() { return new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-'); }
  function _esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

  return { init, load };
})();

window.DWSLogs = DWSLogs;
