/**
 * DWS Loading Manager
 * Full-page loading overlays, inline button spinners, and skeleton
 * placeholder helpers shared by all Sprint 3 pages.
 * Build 005.1 — Sprint 3
 */
'use strict';

const LoadingManager = (() => {
  _injectStyles();
  let overlayEl = null;
  let overlayDepth = 0;

  function showOverlay(message = 'Loading…') {
    overlayDepth++;
    if (!overlayEl) {
      overlayEl = document.createElement('div');
      overlayEl.className = 'dws-loading-overlay';
      overlayEl.innerHTML = `
        <div class="dws-loading-spinner"></div>
        <div class="dws-loading-message">${_esc(message)}</div>
      `;
      document.body.appendChild(overlayEl);
      requestAnimationFrame(() => overlayEl.classList.add('open'));
    } else {
      overlayEl.querySelector('.dws-loading-message').textContent = message;
    }
  }

  function hideOverlay() {
    overlayDepth = Math.max(0, overlayDepth - 1);
    if (overlayDepth > 0 || !overlayEl) return;
    overlayEl.classList.remove('open');
    const el = overlayEl;
    overlayEl = null;
    setTimeout(() => el.remove(), 200);
  }

  function forceHideOverlay() {
    overlayDepth = 0;
    if (overlayEl) { overlayEl.remove(); overlayEl = null; }
  }

  function setButtonLoading(btnOrSelector, on = true) {
    const btn = typeof btnOrSelector === 'string' ? document.querySelector(btnOrSelector) : btnOrSelector;
    if (!btn) return;
    if (on) {
      btn.dataset._label = btn.dataset._label || btn.innerHTML;
      btn.classList.add('loading');
      btn.disabled = true;
    } else {
      btn.classList.remove('loading');
      btn.disabled = false;
    }
  }

  /** Replace a container's content with N skeleton rows/blocks while data loads. */
  function skeleton(container, { rows = 3, height = 14, width = '100%' } = {}) {
    const el = typeof container === 'string' ? document.querySelector(container) : container;
    if (!el) return;
    el.innerHTML = Array.from({ length: rows }, () =>
      `<div class="skeleton" style="height:${height}px;width:${width};margin-bottom:10px;border-radius:var(--radius-md);"></div>`
    ).join('');
  }

  function skeletonCard(container) {
    const el = typeof container === 'string' ? document.querySelector(container) : container;
    if (!el) return;
    el.innerHTML = `
      <div class="skeleton" style="height:14px;width:40%;margin-bottom:14px;"></div>
      <div class="skeleton" style="height:28px;width:60%;margin-bottom:10px;"></div>
      <div class="skeleton" style="height:12px;width:80%;"></div>
    `;
  }

  function _esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

  function _injectStyles() {
    if (document.getElementById('loading-manager-styles')) return;
    const style = document.createElement('style');
    style.id = 'loading-manager-styles';
    style.textContent = `
      .dws-loading-overlay {
        position: fixed; inset: 0; z-index: var(--z-modal, 900);
        background: rgba(7,10,16,0.72); backdrop-filter: blur(3px);
        display: flex; flex-direction: column; align-items: center; justify-content: center;
        gap: var(--sp-4); opacity: 0; pointer-events: none; transition: opacity var(--dur-base);
      }
      .dws-loading-overlay.open { opacity: 1; pointer-events: auto; }
      .dws-loading-spinner {
        width: 40px; height: 40px; border-radius: 50%;
        border: 3px solid var(--clr-border-strong); border-top-color: var(--clr-primary);
        animation: dws-spin 0.8s linear infinite;
      }
      @keyframes dws-spin { to { transform: rotate(360deg); } }
      .dws-loading-message { font-size: var(--text-sm); color: var(--clr-text-secondary); letter-spacing: 0.02em; }
      .spinner-inline {
        display: inline-block; width: 14px; height: 14px; border-radius: 50%;
        border: 2px solid var(--clr-border-strong); border-top-color: var(--clr-primary);
        animation: dws-spin 0.7s linear infinite; vertical-align: middle;
      }
    `;
    document.head.appendChild(style);
  }

  return { showOverlay, hideOverlay, forceHideOverlay, setButtonLoading, skeleton, skeletonCard };
})();

window.LoadingManager = LoadingManager;
