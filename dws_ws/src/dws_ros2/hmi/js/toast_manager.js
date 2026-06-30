/**
 * DWS Toast Manager
 * Queued / stacked toast notifications built on top of the existing
 * .toast / .toast-container CSS (animations.css) and complementary to
 * the simple DWS_UI.Toast helper already shipped in ui_manager.js.
 *
 * Adds: a capped visible stack with overflow queueing, persistent
 * (sticky) toasts, inline action buttons, and a countdown progress rail.
 *
 * Build 005.1 — Sprint 3
 */
'use strict';

const ToastManager = (() => {
  _injectStyles();

  const MAX_VISIBLE = 4;
  const queue = [];
  let visibleCount = 0;
  let container = null;
  let seq = 0;

  const ICONS = {
    success: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`,
    error:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`,
    warning: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
    info:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`,
  };

  function _ensureContainer() {
    if (container) return container;
    container = document.createElement('div');
    container.className = 'toast-container';
    container.setAttribute('aria-live', 'polite');
    document.body.appendChild(container);
    return container;
  }

  /**
   * @param {object} opts
   *  type: success|error|warning|info
   *  title, message
   *  duration: ms, 0 = sticky (no auto dismiss)
   *  action: { label, onClick }
   */
  function push(opts) {
    const item = Object.assign({
      id: 'toast-' + (++seq),
      type: 'info',
      title: '',
      message: '',
      duration: 4500,
      action: null,
    }, opts);

    queue.push(item);
    _drain();
    return item.id;
  }

  function _drain() {
    const c = _ensureContainer();
    while (visibleCount < MAX_VISIBLE && queue.length) {
      const item = queue.shift();
      _render(c, item);
      visibleCount++;
    }
  }

  function _render(c, item) {
    const el = document.createElement('div');
    el.className = `toast ${item.type}`;
    el.id = item.id;
    el.setAttribute('role', 'alert');

    el.innerHTML = `
      <span class="toast-icon">${ICONS[item.type] || ICONS.info}</span>
      <div class="toast-content">
        ${item.title ? `<div class="toast-title">${_esc(item.title)}</div>` : ''}
        ${item.message ? `<div class="toast-message">${_esc(item.message)}</div>` : ''}
        ${item.action ? `<button class="toast-action-btn" type="button">${_esc(item.action.label)}</button>` : ''}
      </div>
      <button class="toast-close" aria-label="Dismiss" type="button">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
      ${item.duration > 0 ? `<div class="toast-progress-rail"><div class="toast-progress-fill"></div></div>` : ''}
    `;

    el.querySelector('.toast-close').addEventListener('click', () => dismiss(item.id));
    if (item.action) {
      el.querySelector('.toast-action-btn').addEventListener('click', () => {
        try { item.action.onClick && item.action.onClick(); } finally { dismiss(item.id); }
      });
    }

    c.appendChild(el);

    if (item.duration > 0) {
      const fill = el.querySelector('.toast-progress-fill');
      if (fill) {
        fill.style.transitionDuration = item.duration + 'ms';
        requestAnimationFrame(() => { fill.style.transform = 'scaleX(0)'; });
      }
      el._timer = setTimeout(() => dismiss(item.id), item.duration);
    }
  }

  function dismiss(id) {
    const el = document.getElementById(id);
    if (!el) return;
    if (el._timer) clearTimeout(el._timer);
    el.classList.add('removing');
    setTimeout(() => {
      el.remove();
      visibleCount = Math.max(0, visibleCount - 1);
      _drain();
    }, 320);
  }

  function dismissAll() {
    if (!container) return;
    Array.from(container.children).forEach(el => dismiss(el.id));
    queue.length = 0;
  }

  function _esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function _injectStyles() {
    if (document.getElementById('toast-manager-styles')) return;
    const style = document.createElement('style');
    style.id = 'toast-manager-styles';
    style.textContent = `
      .toast { position: relative; padding-bottom: var(--sp-5); }
      .toast-action-btn {
        margin-top: var(--sp-2);
        background: transparent;
        border: 1px solid var(--clr-border-strong);
        color: var(--clr-text-primary);
        font-size: var(--text-sm);
        font-weight: var(--weight-medium);
        padding: 4px 12px;
        border-radius: var(--radius-md);
        cursor: pointer;
        transition: background var(--dur-fast), border-color var(--dur-fast);
      }
      .toast-action-btn:hover { background: var(--clr-bg-hover); border-color: var(--clr-primary); }
      .toast-close { background:transparent;border:none;color:var(--clr-text-tertiary);cursor:pointer;padding:4px;border-radius:4px;display:flex;align-items:center;transition:color .15s; }
      .toast-close:hover { color: var(--clr-text-primary); }
      .toast-progress-rail {
        position: absolute; left: 0; right: 0; bottom: 0;
        height: 3px; background: rgba(255,255,255,0.06);
        overflow: hidden; border-radius: 0 0 var(--radius-lg) var(--radius-lg);
      }
      .toast-progress-fill {
        position: absolute; inset: 0;
        background: var(--clr-primary);
        transform-origin: left;
        transform: scaleX(1);
        transition: transform linear;
      }
      .toast.success .toast-progress-fill { background: var(--clr-success); }
      .toast.error   .toast-progress-fill { background: var(--clr-danger); }
      .toast.warning .toast-progress-fill { background: var(--clr-warning); }
    `;
    document.head.appendChild(style);
  }

  return {
    push,
    dismiss,
    dismissAll,
    success: (title, message, opts)  => push({ ...opts, type: 'success', title, message }),
    error:   (title, message, opts)  => push({ ...opts, type: 'error',   title, message }),
    warning: (title, message, opts)  => push({ ...opts, type: 'warning', title, message }),
    info:    (title, message, opts)  => push({ ...opts, type: 'info',    title, message }),
    sticky:  (type, title, message, action) => push({ type, title, message, duration: 0, action }),
  };
})();

window.ToastManager = ToastManager;
