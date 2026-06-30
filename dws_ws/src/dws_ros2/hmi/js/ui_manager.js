/**
 * DWS UI Manager
 * Handles: navigation, sidebar, toast notifications, modals, clock, theme
 * v1.0 Build 005.1
 *
 * NOTE: This file does NOT modify any existing DWS JS function names,
 * DOM IDs used by the backend, WebSocket contracts, or REST endpoints.
 * It wraps and layers on top of the existing hmi/index.html logic.
 */

'use strict';

/* ============================================================
   TOAST NOTIFICATIONS
   ============================================================ */
const Toast = (() => {
  let container = null;

  function _ensureContainer() {
    if (container) return container;
    container = document.createElement('div');
    container.className = 'toast-container';
    container.setAttribute('aria-live', 'polite');
    container.setAttribute('aria-atomic', 'false');
    document.body.appendChild(container);
    return container;
  }

  const ICONS = {
    success: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`,
    error:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`,
    warning: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
    info:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`,
  };

  function show(type, title, message, duration = 4000) {
    const c = _ensureContainer();

    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.setAttribute('role', 'alert');
    el.innerHTML = `
      <span class="toast-icon">${ICONS[type] || ICONS.info}</span>
      <div class="toast-content">
        <div class="toast-title">${title}</div>
        ${message ? `<div class="toast-message">${message}</div>` : ''}
      </div>
      <button class="toast-close" aria-label="Dismiss" style="background:transparent;border:none;color:var(--clr-text-tertiary);cursor:pointer;padding:4px;border-radius:4px;display:flex;align-items:center;transition:color .15s;" onclick="this.closest('.toast').remove()">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    `;

    c.appendChild(el);

    if (duration > 0) {
      setTimeout(() => {
        el.classList.add('removing');
        el.addEventListener('animationend', () => el.remove(), { once: true });
        setTimeout(() => el.remove(), 500);
      }, duration);
    }

    return el;
  }

  return {
    success: (title, msg, dur) => show('success', title, msg, dur),
    error:   (title, msg, dur) => show('error',   title, msg, dur),
    warning: (title, msg, dur) => show('warning', title, msg, dur),
    info:    (title, msg, dur) => show('info',    title, msg, dur),
  };
})();

/* ============================================================
   SIDEBAR / NAVIGATION
   ============================================================ */
const Navigation = (() => {
  const KEY_COLLAPSED = 'dws_sidebar_collapsed';
  let shell = null;

  function init() {
    shell = document.querySelector('.app-shell');
    if (!shell) return;

    // Restore collapsed state
    if (localStorage.getItem(KEY_COLLAPSED) === '1') {
      shell.classList.add('sidebar-collapsed');
    }

    // Collapse toggle button
    const btn = document.querySelector('.topbar-collapse-btn');
    if (btn) btn.addEventListener('click', toggleSidebar);

    // Active nav item
    _markActive();

    // Nav item clicks
    shell.querySelectorAll('.nav-item[data-page]').forEach(item => {
      item.addEventListener('click', () => {
        const page = item.getAttribute('data-page');
        if (page) navigateTo(page);
      });
    });
  }

  function toggleSidebar() {
    if (!shell) return;
    const collapsed = shell.classList.toggle('sidebar-collapsed');
    localStorage.setItem(KEY_COLLAPSED, collapsed ? '1' : '0');
  }

  function _markActive() {
    const current = window.location.pathname.split('/').pop().replace('.html', '') || 'dashboard';
    shell.querySelectorAll('.nav-item[data-page]').forEach(item => {
      item.classList.toggle('active', item.getAttribute('data-page') === current);
    });
  }

  function navigateTo(page) {
    // In SPA mode (single index.html with sections), show/hide sections
    // In multi-page mode, navigate to page.html
    const sections = document.querySelectorAll('[data-section]');
    if (sections.length > 0) {
      // SPA: show matching section
      sections.forEach(s => {
        const show = s.getAttribute('data-section') === page;
        s.style.display = show ? '' : 'none';
      });

      // Update breadcrumb
      const breadcrumb = document.querySelector('.bc-current');
      if (breadcrumb) breadcrumb.textContent = _pageName(page);

      // Update active
      shell.querySelectorAll('.nav-item[data-page]').forEach(item => {
        item.classList.toggle('active', item.getAttribute('data-page') === page);
      });

      window.history.pushState({}, '', '#' + page);
    } else {
      window.location.href = page + '.html';
    }
  }

  function _pageName(page) {
    const names = {
      dashboard:    'Dashboard',
      live:         'Live Measurement',
      transactions: 'Transactions',
      reports:      'Reports',
      diagnostics:  'Diagnostics',
      calibration:  'Calibration',
      users:        'User Management',
      settings:     'Settings',
      logs:         'System Logs',
      about:        'About',
    };
    return names[page] || page;
  }

  return { init, toggleSidebar, navigateTo };
})();

/* ============================================================
   LIVE CLOCK
   ============================================================ */
const Clock = (() => {
  let el = null;
  let interval = null;

  function init(selector = '.topbar-time') {
    el = document.querySelector(selector);
    if (!el) return;
    tick();
    interval = setInterval(tick, 1000);
  }

  function tick() {
    if (!el) return;
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    el.textContent =
      `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  }

  function destroy() {
    if (interval) clearInterval(interval);
  }

  return { init, destroy };
})();

/* ============================================================
   MODALS
   ============================================================ */
const Modal = (() => {
  function open(id) {
    const overlay = document.getElementById(id);
    if (!overlay) return;
    overlay.classList.add('open');
    overlay.setAttribute('aria-hidden', 'false');
    // Focus first focusable inside modal
    const first = overlay.querySelector('button, input, select, textarea, [tabindex]:not([tabindex="-1"])');
    if (first) setTimeout(() => first.focus(), 100);
    // Close on overlay click
    overlay.addEventListener('click', e => {
      if (e.target === overlay) close(id);
    }, { once: true });
    // ESC key
    const esc = e => { if (e.key === 'Escape') close(id); };
    document.addEventListener('keydown', esc, { once: true });
  }

  function close(id) {
    const overlay = document.getElementById(id);
    if (!overlay) return;
    overlay.classList.remove('open');
    overlay.setAttribute('aria-hidden', 'true');
  }

  function confirm({
    title = 'Confirm',
    message = 'Are you sure?',
    type = 'danger',
    confirmLabel = 'Confirm',
    cancelLabel = 'Cancel',
    onConfirm = () => {},
    onCancel  = () => {},
  }) {
    // Dynamically create a confirm modal
    const id = 'modal-confirm-' + Date.now();
    const icons = {
      danger:  `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
      warning: `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
    };

    const el = document.createElement('div');
    el.id = id;
    el.className = 'modal-overlay';
    el.setAttribute('aria-hidden', 'true');
    el.setAttribute('role', 'dialog');
    el.innerHTML = `
      <div class="modal" style="max-width:440px">
        <div class="modal-body" style="padding:var(--sp-8);text-align:center">
          <div class="confirm-icon ${type}" style="margin:0 auto var(--sp-4)">
            ${icons[type] || icons.danger}
          </div>
          <div class="confirm-title">${title}</div>
          <p class="confirm-message" style="margin-top:var(--sp-2)">${message}</p>
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary" id="${id}-cancel">${cancelLabel}</button>
          <button class="btn btn-${type}" id="${id}-confirm">${confirmLabel}</button>
        </div>
      </div>
    `;
    document.body.appendChild(el);

    document.getElementById(`${id}-cancel`).addEventListener('click', () => {
      onCancel();
      el.remove();
    });
    document.getElementById(`${id}-confirm`).addEventListener('click', () => {
      onConfirm();
      el.remove();
    });
    el.addEventListener('click', e => { if (e.target === el) { onCancel(); el.remove(); } });

    requestAnimationFrame(() => el.classList.add('open'));
    return { close: () => el.remove() };
  }

  // Wire up all [data-modal-open] and [data-modal-close] buttons
  function initDelegation() {
    document.addEventListener('click', e => {
      const opener = e.target.closest('[data-modal-open]');
      if (opener) open(opener.getAttribute('data-modal-open'));

      const closer = e.target.closest('[data-modal-close]');
      if (closer) close(closer.getAttribute('data-modal-close'));
    });
  }

  return { open, close, confirm, initDelegation };
})();

/* ============================================================
   TABLE UTILITIES
   ============================================================ */
const DataTable = (() => {
  /**
   * Enhance a <table> element with:
   * - Client-side search
   * - Sortable columns
   * - Pagination
   *
   * @param {string} tableId  - ID of the <table> element
   * @param {object} options
   */
  function enhance(tableId, options = {}) {
    const table = document.getElementById(tableId);
    if (!table) return;

    const {
      pageSize   = 20,
      searchable = true,
    } = options;

    const tbody  = table.querySelector('tbody');
    const wrap   = table.closest('.table-wrap');
    let allRows  = Array.from(tbody ? tbody.querySelectorAll('tr') : []);
    let filtered = [...allRows];
    let currentPage = 1;
    let sortCol     = -1;
    let sortAsc     = true;

    // ── SEARCH ──────────────────────────────────────────────
    if (searchable && wrap) {
      const input = wrap.querySelector('.table-search input');
      if (input) {
        input.addEventListener('input', () => {
          const q = input.value.trim().toLowerCase();
          filtered = q
            ? allRows.filter(r => r.textContent.toLowerCase().includes(q))
            : [...allRows];
          currentPage = 1;
          render();
        });
      }
    }

    // ── SORT ─────────────────────────────────────────────────
    table.querySelectorAll('th.sortable').forEach((th, i) => {
      th.addEventListener('click', () => {
        if (sortCol === i) { sortAsc = !sortAsc; }
        else { sortCol = i; sortAsc = true; }

        table.querySelectorAll('th').forEach(t => {
          t.classList.remove('sort-asc', 'sort-desc');
        });
        th.classList.add(sortAsc ? 'sort-asc' : 'sort-desc');

        filtered.sort((a, b) => {
          const va = a.cells[i]?.textContent.trim() || '';
          const vb = b.cells[i]?.textContent.trim() || '';
          const num_a = parseFloat(va), num_b = parseFloat(vb);
          if (!isNaN(num_a) && !isNaN(num_b)) {
            return sortAsc ? num_a - num_b : num_b - num_a;
          }
          return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
        });

        currentPage = 1;
        render();
      });
    });

    // ── PAGINATION ────────────────────────────────────────────
    function render() {
      const total = filtered.length;
      const totalPages = Math.max(1, Math.ceil(total / pageSize));
      currentPage = Math.min(currentPage, totalPages);
      const start = (currentPage - 1) * pageSize;
      const end   = start + pageSize;

      allRows.forEach(r => { r.style.display = 'none'; });
      filtered.slice(start, end).forEach(r => { r.style.display = ''; });

      _updatePagination(wrap, { currentPage, totalPages, total, start, end });
    }

    function _updatePagination(wrap, { currentPage, totalPages, total, start, end }) {
      if (!wrap) return;
      const info = wrap.querySelector('.pagination-info');
      if (info) {
        info.textContent = total === 0
          ? 'No records'
          : `${start + 1}–${Math.min(end, total)} of ${total}`;
      }

      const controls = wrap.querySelector('.pagination-controls');
      if (!controls) return;
      controls.innerHTML = '';

      const prev = _mkBtn('‹', currentPage <= 1, () => { currentPage--; render(); });
      controls.appendChild(prev);

      const maxPages = 5;
      let pages = [];
      if (totalPages <= maxPages) {
        pages = Array.from({ length: totalPages }, (_, i) => i + 1);
      } else {
        pages = [1];
        let lo = Math.max(2, currentPage - 1);
        let hi = Math.min(totalPages - 1, currentPage + 1);
        if (lo > 2) pages.push('…');
        for (let p = lo; p <= hi; p++) pages.push(p);
        if (hi < totalPages - 1) pages.push('…');
        pages.push(totalPages);
      }

      pages.forEach(p => {
        if (p === '…') {
          const s = document.createElement('span');
          s.textContent = '…';
          s.style.cssText = 'padding:0 4px;color:var(--clr-text-tertiary);align-self:center';
          controls.appendChild(s);
        } else {
          const b = _mkBtn(String(p), false, () => { currentPage = p; render(); });
          if (p === currentPage) b.classList.add('active');
          controls.appendChild(b);
        }
      });

      const next = _mkBtn('›', currentPage >= totalPages, () => { currentPage++; render(); });
      controls.appendChild(next);
    }

    function _mkBtn(label, disabled, onClick) {
      const b = document.createElement('button');
      b.className = 'pagination-btn';
      b.textContent = label;
      b.disabled = disabled;
      if (!disabled) b.addEventListener('click', onClick);
      return b;
    }

    // Re-pull rows from tbody (useful after dynamic update)
    function refresh() {
      allRows  = Array.from(tbody ? tbody.querySelectorAll('tr') : []);
      filtered = [...allRows];
      currentPage = 1;
      render();
    }

    render();
    return { refresh };
  }

  return { enhance };
})();

/* ============================================================
   HEALTH DOT UPDATER
   (Reads DWS /health endpoint and updates topbar indicator)
   ============================================================ */
const HealthIndicator = (() => {
  let dot = null;
  let interval = null;

  function init(apiBase) {
    dot = document.querySelector('.topbar-health-dot');
    if (!dot || !apiBase) return;
    _poll(apiBase);
    interval = setInterval(() => _poll(apiBase), 10000);
  }

  async function _poll(apiBase) {
    try {
      const r = await fetch(`${apiBase}/health`, { signal: AbortSignal.timeout(5000) });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      const status = data.status || 'unknown';
      if (!dot) return;
      dot.className = 'topbar-health-dot';
      if (status === 'healthy') dot.classList.add('healthy');
      else if (status === 'degraded') dot.classList.add('warning');
      else dot.classList.add('error');
      dot.title = `System: ${status}`;
    } catch {
      if (dot) {
        dot.className = 'topbar-health-dot error';
        dot.title = 'System: unreachable';
      }
    }
  }

  function destroy() {
    if (interval) clearInterval(interval);
  }

  return { init, destroy };
})();

/* ============================================================
   NUMBER ANIMATION  (count up to a target value)
   ============================================================ */
function animateNumber(el, target, { duration = 800, decimals = 0, suffix = '' } = {}) {
  if (!el) return;
  const start = parseFloat(el.dataset.value || '0') || 0;
  const diff  = target - start;
  const t0    = performance.now();

  el.dataset.value = String(target);

  function step(now) {
    const progress = Math.min(1, (now - t0) / duration);
    const ease     = 1 - Math.pow(1 - progress, 3); // cubic ease-out
    const value    = start + diff * ease;
    el.textContent = value.toFixed(decimals) + suffix;
    if (progress < 1) requestAnimationFrame(step);
  }

  requestAnimationFrame(step);
}

/* ============================================================
   FORM UTILITIES
   ============================================================ */

/**
 * Show/hide password toggle for .input-icon-wrap elements
 */
function initPasswordToggles() {
  document.querySelectorAll('.btn-show-pass').forEach(btn => {
    btn.addEventListener('click', () => {
      const wrap  = btn.closest('.input-icon-wrap');
      const input = wrap?.querySelector('input');
      if (!input) return;
      const isText = input.type === 'text';
      input.type = isText ? 'password' : 'text';
      // swap eye icon
      btn.innerHTML = isText
        ? `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>`
        : `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>`;
    });
  });
}

/* ============================================================
   STATUS BADGE HELPER
   ============================================================ */
function makeStatusBadge(status) {
  const map = {
    online:      ['online',      'Online'],
    offline:     ['offline',     'Offline'],
    warning:     ['warning',     'Warning'],
    error:       ['error',       'Error'],
    calibrating: ['calibrating', 'Calibrating'],
    processing:  ['processing',  'Processing'],
    ready:       ['ready',       'Ready'],
    locked:      ['locked',      'Locked'],
  };
  const [cls, label] = map[status] || ['offline', status];
  return `<span class="status-badge ${cls}">${label}</span>`;
}

/* ============================================================
   AUTO-INIT
   ============================================================ */
document.addEventListener('DOMContentLoaded', () => {
  Navigation.init();
  Clock.init();
  Modal.initDelegation();
  initPasswordToggles();

  // Health indicator
  const apiBase = `http://${window.location.hostname}:8000`;
  HealthIndicator.init(apiBase);
});

/* ── EXPORTS (for usage in other modules) ────────────────── */
window.DWS_UI = {
  Toast,
  Navigation,
  Clock,
  Modal,
  DataTable,
  HealthIndicator,
  animateNumber,
  makeStatusBadge,
};
