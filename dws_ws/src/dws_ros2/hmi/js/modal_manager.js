/**
 * DWS Modal Manager
 * Builds on top of the existing .modal-overlay / .modal CSS (tables.css)
 * and complements the static-DOM Modal helper in ui_manager.js by
 * supporting fully dynamic modals: arbitrary content, size variants,
 * side drawers, and multi-step wizards.
 *
 * Build 005.1 — Sprint 3
 */
'use strict';

const ModalManager = (() => {
  _injectStyles();
  let activeStack = [];

  /**
   * @param {object} opts
   *  title, bodyHtml, footerHtml, size: sm|md|lg|xl, closable, onClose
   */
  function open(opts = {}) {
    const {
      title = '',
      bodyHtml = '',
      footerHtml = '',
      size = 'md',
      closable = true,
      onClose = null,
      drawer = false,
    } = opts;

    const id = 'dyn-modal-' + Date.now() + Math.floor(Math.random() * 1000);
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay' + (drawer ? ' modal-drawer-overlay' : '');
    overlay.id = id;
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.innerHTML = `
      <div class="modal modal-${size}${drawer ? ' modal-drawer' : ''}">
        <div class="modal-header">
          <div class="modal-title">${_esc(title)}</div>
          ${closable ? `<button class="modal-close" type="button" aria-label="Close">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>` : ''}
        </div>
        <div class="modal-body">${bodyHtml}</div>
        ${footerHtml ? `<div class="modal-footer">${footerHtml}</div>` : ''}
      </div>
    `;
    document.body.appendChild(overlay);

    const close = () => {
      overlay.classList.remove('open');
      setTimeout(() => {
        overlay.remove();
        activeStack = activeStack.filter(m => m.id !== id);
        if (onClose) onClose();
      }, 220);
    };

    if (closable) {
      overlay.querySelector('.modal-close').addEventListener('click', close);
      overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
      const esc = e => { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc); } };
      document.addEventListener('keydown', esc);
    }

    requestAnimationFrame(() => overlay.classList.add('open'));
    const handle = { id, el: overlay, close, setBody: html => { overlay.querySelector('.modal-body').innerHTML = html; } };
    activeStack.push(handle);
    return handle;
  }

  function closeTop() {
    if (activeStack.length) activeStack[activeStack.length - 1].close();
  }

  function closeAll() {
    [...activeStack].forEach(m => m.close());
  }

  /**
   * Multi-step wizard modal.
   * @param {object} opts
   *  title, steps: [{ label, render(container, ctx), validate(ctx) }]
   *  onComplete(ctx), size
   */
  function wizard(opts = {}) {
    const { title = 'Wizard', steps = [], onComplete = () => {}, size = 'lg' } = opts;
    let idx = 0;
    const ctx = {};

    const stepsHtml = steps.map((s, i) => `
      <div class="wizard-step-dot ${i === 0 ? 'active' : ''}" data-step="${i}">
        <span class="wizard-step-num">${i + 1}</span>
        <span class="wizard-step-label">${_esc(s.label)}</span>
      </div>
    `).join('<div class="wizard-step-connector"></div>');

    const handle = open({
      title,
      size,
      bodyHtml: `
        <div class="wizard-steps">${stepsHtml}</div>
        <div class="wizard-content" id="${'wz-body'}"></div>
      `,
      footerHtml: `
        <button class="btn btn-secondary" id="wz-back" disabled>Back</button>
        <div style="flex:1"></div>
        <button class="btn btn-primary" id="wz-next">Next</button>
      `,
    });

    const body = handle.el.querySelector('.wizard-content');
    const backBtn = handle.el.querySelector('#wz-back');
    const nextBtn = handle.el.querySelector('#wz-next');

    function renderStep() {
      handle.el.querySelectorAll('.wizard-step-dot').forEach((d, i) => {
        d.classList.toggle('active', i === idx);
        d.classList.toggle('complete', i < idx);
      });
      body.innerHTML = '';
      steps[idx].render(body, ctx);
      backBtn.disabled = idx === 0;
      nextBtn.textContent = idx === steps.length - 1 ? 'Finish' : 'Next';
    }

    nextBtn.addEventListener('click', () => {
      const step = steps[idx];
      if (step.validate && !step.validate(ctx)) return;
      if (idx === steps.length - 1) {
        onComplete(ctx);
        handle.close();
        return;
      }
      idx++;
      renderStep();
    });

    backBtn.addEventListener('click', () => {
      if (idx === 0) return;
      idx--;
      renderStep();
    });

    renderStep();
    return handle;
  }

  function confirm(opts = {}) {
    const {
      title = 'Confirm action', message = 'Are you sure?', type = 'danger',
      confirmLabel = 'Confirm', cancelLabel = 'Cancel', onConfirm = () => {}, onCancel = () => {},
    } = opts;
    const handle = open({
      title: '',
      size: 'sm',
      bodyHtml: `
        <div style="text-align:center;padding:var(--sp-2) 0;">
          <div class="confirm-icon ${type}" style="margin:0 auto var(--sp-4);">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
          </div>
          <div class="confirm-title">${_esc(title)}</div>
          <p class="confirm-message" style="margin-top:var(--sp-2);">${_esc(message)}</p>
        </div>
      `,
      footerHtml: `
        <button class="btn btn-secondary" id="cf-cancel">${_esc(cancelLabel)}</button>
        <button class="btn btn-${type}" id="cf-confirm">${_esc(confirmLabel)}</button>
      `,
    });
    handle.el.querySelector('.modal-header').style.display = 'none';
    handle.el.querySelector('#cf-cancel').addEventListener('click', () => { onCancel(); handle.close(); });
    handle.el.querySelector('#cf-confirm').addEventListener('click', () => { onConfirm(); handle.close(); });
    return handle;
  }

  function _esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function _injectStyles() {
    if (document.getElementById('modal-manager-styles')) return;
    const style = document.createElement('style');
    style.id = 'modal-manager-styles';
    style.textContent = `
      .modal-drawer-overlay { justify-content: flex-end; padding: 0; align-items: stretch; }
      .modal-drawer {
        max-width: 420px; width: 420px; max-height: 100vh; height: 100vh;
        border-radius: 0; transform: translateX(24px);
      }
      .modal-drawer-overlay.open .modal-drawer { transform: translateX(0); }
      .wizard-steps {
        display: flex; align-items: center; margin-bottom: var(--sp-6);
        padding-bottom: var(--sp-4); border-bottom: 1px solid var(--clr-border);
      }
      .wizard-step-dot { display: flex; align-items: center; gap: var(--sp-2); flex-shrink: 0; }
      .wizard-step-num {
        width: 26px; height: 26px; border-radius: 50%; display: flex; align-items: center;
        justify-content: center; background: var(--clr-bg-raised); border: 1px solid var(--clr-border-strong);
        color: var(--clr-text-tertiary); font-size: var(--text-sm); font-weight: var(--weight-semibold);
        flex-shrink: 0; transition: background var(--dur-base), border-color var(--dur-base), color var(--dur-base);
      }
      .wizard-step-dot.active .wizard-step-num { background: var(--clr-primary); border-color: var(--clr-primary); color: white; }
      .wizard-step-dot.complete .wizard-step-num { background: var(--clr-success); border-color: var(--clr-success); color: #0b0e13; }
      .wizard-step-label { font-size: var(--text-sm); color: var(--clr-text-tertiary); white-space: nowrap; }
      .wizard-step-dot.active .wizard-step-label { color: var(--clr-text-primary); font-weight: var(--weight-medium); }
      .wizard-step-connector { flex: 1; height: 1px; background: var(--clr-border); margin: 0 var(--sp-3); min-width: 16px; }
    `;
    document.head.appendChild(style);
  }

  return { open, closeTop, closeAll, wizard, confirm };
})();

window.ModalManager = ModalManager;
