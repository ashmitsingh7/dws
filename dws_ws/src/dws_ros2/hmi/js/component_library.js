/**
 * DWS Component Library
 * Markup factories for the reusable UI atoms shared by every Sprint 3
 * page: status cards, metric/health/stat cards, badges, chips, alerts,
 * accordions, tabs, breadcrumb, search boxes, and toolbars.
 *
 * These are *markup builders* (return HTML strings) that lean on the
 * existing CSS already shipped (dashboard.css, tables.css, animations.css)
 * plus a small injected stylesheet for the handful of atoms that don't
 * yet exist anywhere (chip, alert, accordion, tabs).
 *
 * Build 005.1 — Sprint 3
 */
'use strict';

const ComponentLibrary = (() => {
  _injectStyles();
  let _uid = 0;

  /* ============================================================
     STATUS CARD — icon + label + status badge, used in health grids
     ============================================================ */
  function statusCard({ icon, label, status = 'offline', detail = '', id = '' }) {
    return `
      <div class="status-list-item dws-status-card" ${id ? `id="${id}"` : ''}>
        <span class="s-icon"><svg class="icon icon-md"><use href="#icon-${icon}"/></svg></span>
        <span class="s-name">${_esc(label)}</span>
        ${detail ? `<span class="s-detail">${_esc(detail)}</span>` : ''}
        <span class="dws-status-badge-slot">${makeStatusBadge(status)}</span>
      </div>
    `;
  }

  /* ============================================================
     METRIC CARD — KPI-style card (re-uses .kpi-card)
     ============================================================ */
  function metricCard({ label, value, unit = '', icon, accent = 'primary', delta = '', id = '' }) {
    const valId = id || `metric-${++_uid}`;
    return `
      <div class="kpi-card accent-${accent}">
        <div class="kpi-header">
          <span class="kpi-label">${_esc(label)}</span>
          ${icon ? `<div class="kpi-icon ${accent}"><svg class="icon icon-md"><use href="#icon-${icon}"/></svg></div>` : ''}
        </div>
        <div class="kpi-value"><span id="${valId}">${value ?? '—'}</span>${unit ? ` <span class="unit">${_esc(unit)}</span>` : ''}</div>
        ${delta ? `<div class="kpi-delta">${_esc(delta)}</div>` : ''}
      </div>
    `;
  }

  /* ============================================================
     HEALTH CARD — component health w/ inline sparkline canvas slot
     ============================================================ */
  function healthCard({ icon, title, status = 'offline', metrics = [], canvasId = '' }) {
    return `
      <div class="form-section dws-health-card">
        <div class="form-section-header">
          <span class="icon icon-md text-secondary"><svg><use href="#icon-${icon}"/></svg></span>
          <span class="form-section-title" style="flex:1;">${_esc(title)}</span>
          ${makeStatusBadge(status)}
        </div>
        <div class="form-section-body" style="gap:var(--sp-3);">
          ${metrics.map(m => `
            <div class="kv-row" style="display:flex;justify-content:space-between;font-size:var(--text-sm);">
              <span class="text-secondary">${_esc(m.label)}</span>
              <span class="mono">${_esc(m.value)}</span>
            </div>
          `).join('')}
          ${canvasId ? `<canvas id="${canvasId}" style="width:100%;height:48px;margin-top:var(--sp-2);"></canvas>` : ''}
        </div>
      </div>
    `;
  }

  /* ============================================================
     STAT CARD — big number + caption, compact variant of metric card
     ============================================================ */
  function statCard({ value, caption, tone = '' }) {
    return `
      <div class="measurement-block">
        <div class="m-label">${_esc(caption)}</div>
        <div class="m-value ${tone}">${value}</div>
      </div>
    `;
  }

  /* ============================================================
     BADGE / CHIP / ALERT
     ============================================================ */
  function makeStatusBadge(status) {
    return window.DWS_UI?.makeStatusBadge(status) || `<span class="status-badge ${status}">${status}</span>`;
  }

  function chip({ label, tone = 'default', removable = false, id = '' }) {
    return `
      <span class="dws-chip tone-${tone}" ${id ? `id="${id}"` : ''}>
        ${_esc(label)}
        ${removable ? `<button class="dws-chip-remove" type="button" aria-label="Remove" onclick="this.closest('.dws-chip').remove()">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>` : ''}
      </span>
    `;
  }

  function alert({ type = 'info', title = '', message = '', dismissible = true }) {
    const icons = {
      info:    `<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>`,
      success: `<polyline points="20 6 9 17 4 12"/>`,
      warning: `<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>`,
      error:   `<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>`,
    };
    return `
      <div class="dws-alert tone-${type}">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${icons[type] || icons.info}</svg>
        <div class="dws-alert-content">
          ${title ? `<div class="dws-alert-title">${_esc(title)}</div>` : ''}
          ${message ? `<div class="dws-alert-message">${_esc(message)}</div>` : ''}
        </div>
        ${dismissible ? `<button class="dws-alert-close" type="button" onclick="this.closest('.dws-alert').remove()">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>` : ''}
      </div>
    `;
  }

  /* ============================================================
     ACCORDION
     ============================================================ */
  function accordion(items, opts = {}) {
    const groupId = opts.id || `acc-${++_uid}`;
    return `
      <div class="dws-accordion" id="${groupId}">
        ${items.map((it, i) => `
          <div class="dws-accordion-item ${it.open ? 'open' : ''}">
            <button class="dws-accordion-trigger" type="button" data-idx="${i}">
              <span>${_esc(it.title)}</span>
              <svg class="dws-accordion-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg>
            </button>
            <div class="dws-accordion-panel"><div class="dws-accordion-panel-inner">${it.bodyHtml}</div></div>
          </div>
        `).join('')}
      </div>
    `;
  }

  function bindAccordion(groupSelector) {
    document.querySelectorAll(`${groupSelector} .dws-accordion-trigger`).forEach(btn => {
      btn.addEventListener('click', () => {
        const item = btn.closest('.dws-accordion-item');
        item.classList.toggle('open');
      });
    });
  }

  /* ============================================================
     TABS
     ============================================================ */
  function tabs(items, opts = {}) {
    const groupId = opts.id || `tabs-${++_uid}`;
    return `
      <div class="dws-tabs" id="${groupId}">
        <div class="dws-tabs-list" role="tablist">
          ${items.map((it, i) => `
            <button class="dws-tab-btn ${i === 0 ? 'active' : ''}" type="button" data-tab="${it.key}" role="tab">
              ${it.icon ? `<svg class="icon icon-sm"><use href="#icon-${it.icon}"/></svg>` : ''}${_esc(it.label)}
            </button>
          `).join('')}
        </div>
        ${items.map((it, i) => `
          <div class="dws-tab-panel" data-panel="${it.key}" style="${i === 0 ? '' : 'display:none;'}">${it.bodyHtml}</div>
        `).join('')}
      </div>
    `;
  }

  function bindTabs(groupSelector) {
    const root = document.querySelector(groupSelector);
    if (!root) return;
    root.querySelectorAll('.dws-tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const key = btn.getAttribute('data-tab');
        root.querySelectorAll('.dws-tab-btn').forEach(b => b.classList.toggle('active', b === btn));
        root.querySelectorAll('.dws-tab-panel').forEach(p => {
          p.style.display = p.getAttribute('data-panel') === key ? '' : 'none';
        });
      });
    });
  }

  /* ============================================================
     BREADCRUMB / SEARCH BOX / TOOLBAR (thin wrappers matching
     the markup already established in navigation.css / tables.css)
     ============================================================ */
  function breadcrumb(trail) {
    return `
      <div class="topbar-breadcrumb">
        ${trail.map((t, i) => `${i > 0 ? '<span class="bc-sep">›</span>' : ''}<span class="${i === trail.length - 1 ? 'bc-current' : ''}">${_esc(t)}</span>`).join('')}
      </div>
    `;
  }

  function searchBox({ id, placeholder = 'Search…' }) {
    return `
      <div class="table-search">
        <span class="search-icon">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        </span>
        <input id="${id}" type="search" placeholder="${_esc(placeholder)}" aria-label="${_esc(placeholder)}">
      </div>
    `;
  }

  function toolbar({ title = '', leftHtml = '', rightHtml = '' }) {
    return `
      <div class="table-toolbar">
        ${title ? `<span class="table-title">${_esc(title)}</span>` : ''}
        ${leftHtml}
        <div class="table-toolbar-spacer"></div>
        ${rightHtml}
      </div>
    `;
  }

  function _esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function _injectStyles() {
    if (document.getElementById('component-library-styles')) return;
    const style = document.createElement('style');
    style.id = 'component-library-styles';
    style.textContent = `
      /* ── CHIP ── */
      .dws-chip {
        display: inline-flex; align-items: center; gap: 6px;
        padding: 3px 10px; border-radius: var(--radius-pill);
        font-size: var(--text-xs); font-weight: var(--weight-medium);
        background: var(--clr-bg-raised); color: var(--clr-text-secondary);
        border: 1px solid var(--clr-border);
      }
      .dws-chip.tone-primary { background: var(--clr-primary-glow); color: var(--clr-primary); border-color: transparent; }
      .dws-chip.tone-success { background: var(--clr-success-bg); color: var(--clr-success); border-color: transparent; }
      .dws-chip.tone-warning { background: var(--clr-warning-bg); color: var(--clr-warning); border-color: transparent; }
      .dws-chip.tone-danger  { background: var(--clr-danger-bg);  color: var(--clr-danger);  border-color: transparent; }
      .dws-chip-remove { background: transparent; border: none; color: inherit; cursor: pointer; display: flex; opacity: 0.7; }
      .dws-chip-remove:hover { opacity: 1; }

      /* ── ALERT ── */
      .dws-alert {
        display: flex; gap: var(--sp-3); align-items: flex-start;
        padding: var(--sp-3) var(--sp-4); border-radius: var(--radius-md);
        border: 1px solid var(--clr-border); background: var(--clr-bg-surface);
        margin-bottom: var(--sp-3);
      }
      .dws-alert.tone-info    { border-color: rgba(56,189,248,0.35);  background: var(--clr-info-bg);    color: var(--clr-info); }
      .dws-alert.tone-success { border-color: rgba(34,197,94,0.35);   background: var(--clr-success-bg); color: var(--clr-success); }
      .dws-alert.tone-warning { border-color: rgba(245,158,11,0.35);  background: var(--clr-warning-bg); color: var(--clr-warning); }
      .dws-alert.tone-error   { border-color: rgba(239,68,68,0.35);   background: var(--clr-danger-bg);  color: var(--clr-danger); }
      .dws-alert-content { flex: 1; }
      .dws-alert-title { font-weight: var(--weight-semibold); font-size: var(--text-sm); color: var(--clr-text-primary); }
      .dws-alert-message { font-size: var(--text-sm); color: var(--clr-text-secondary); margin-top: 2px; }
      .dws-alert-close { background: transparent; border: none; color: inherit; opacity: 0.6; cursor: pointer; flex-shrink: 0; }
      .dws-alert-close:hover { opacity: 1; }

      /* ── ACCORDION ── */
      .dws-accordion-item { border: 1px solid var(--clr-border); border-radius: var(--radius-md); margin-bottom: var(--sp-2); overflow: hidden; }
      .dws-accordion-trigger {
        width: 100%; display: flex; align-items: center; justify-content: space-between;
        background: var(--clr-bg-surface); border: none; color: var(--clr-text-primary);
        padding: var(--sp-3) var(--sp-4); font-size: var(--text-base); font-weight: var(--weight-medium);
        cursor: pointer; text-align: left;
      }
      .dws-accordion-trigger:hover { background: var(--clr-bg-hover); }
      .dws-accordion-chevron { transition: transform var(--dur-base); color: var(--clr-text-tertiary); }
      .dws-accordion-item.open .dws-accordion-chevron { transform: rotate(180deg); }
      .dws-accordion-panel { max-height: 0; overflow: hidden; transition: max-height var(--dur-base) var(--ease-out); }
      .dws-accordion-item.open .dws-accordion-panel { max-height: 600px; }
      .dws-accordion-panel-inner { padding: var(--sp-4); border-top: 1px solid var(--clr-border); font-size: var(--text-sm); color: var(--clr-text-secondary); }

      /* ── TABS ── */
      .dws-tabs-list { display: flex; gap: var(--sp-1); border-bottom: 1px solid var(--clr-border); margin-bottom: var(--sp-4); overflow-x: auto; }
      .dws-tab-btn {
        display: flex; align-items: center; gap: var(--sp-2);
        background: transparent; border: none; border-bottom: 2px solid transparent;
        color: var(--clr-text-secondary); padding: var(--sp-3) var(--sp-4);
        font-size: var(--text-base); font-weight: var(--weight-medium); cursor: pointer; white-space: nowrap;
        transition: color var(--dur-fast), border-color var(--dur-fast);
      }
      .dws-tab-btn:hover { color: var(--clr-text-primary); }
      .dws-tab-btn.active { color: var(--clr-primary); border-color: var(--clr-primary); }

      .dws-status-card .dws-status-badge-slot { margin-left: auto; }
    `;
    document.head.appendChild(style);
  }

  return {
    statusCard, metricCard, healthCard, statCard,
    makeStatusBadge, chip, alert,
    accordion, bindAccordion,
    tabs, bindTabs,
    breadcrumb, searchBox, toolbar,
  };
})();

window.ComponentLibrary = ComponentLibrary;
