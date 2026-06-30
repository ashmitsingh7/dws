/**
 * DWS Form Components
 * Reusable field builders (text/number/select/toggle/range/textarea),
 * a lightweight FormBuilder for grouped settings forms, and shared
 * client-side validators. Renders using the existing .form-control /
 * .toggle / .form-section classes (tables.css).
 * Build 005.1 — Sprint 3
 */
'use strict';

const FormComponents = (() => {

  let _uid = 0;
  const nextId = (prefix) => `${prefix}-${++_uid}`;

  function field({ label, hint, error, required, inputHtml, id }) {
    return `
      <div class="form-group" data-field="${id || ''}">
        ${label ? `<label class="form-label" for="${id || ''}">${_esc(label)} ${required ? '<span class="required">*</span>' : ''}</label>` : ''}
        ${inputHtml}
        ${hint ? `<div class="form-hint">${_esc(hint)}</div>` : ''}
        ${error ? `<div class="form-error">${_esc(error)}</div>` : ''}
      </div>
    `;
  }

  function text(opts = {}) {
    const id = opts.id || nextId('fc-text');
    return field({ ...opts, id, inputHtml: `
      <input type="${opts.type || 'text'}" id="${id}" class="form-control" name="${opts.name || id}"
        placeholder="${_esc(opts.placeholder || '')}" value="${_esc(opts.value ?? '')}"
        ${opts.required ? 'required' : ''} ${opts.disabled ? 'disabled' : ''} ${opts.pattern ? `pattern="${opts.pattern}"` : ''}>
    ` });
  }

  function number(opts = {}) {
    const id = opts.id || nextId('fc-num');
    return field({ ...opts, id, inputHtml: `
      <input type="number" id="${id}" class="form-control" name="${opts.name || id}"
        value="${opts.value ?? ''}" min="${opts.min ?? ''}" max="${opts.max ?? ''}" step="${opts.step ?? 'any'}"
        ${opts.required ? 'required' : ''} ${opts.disabled ? 'disabled' : ''}>
    ` });
  }

  function textarea(opts = {}) {
    const id = opts.id || nextId('fc-ta');
    return field({ ...opts, id, inputHtml: `
      <textarea id="${id}" class="form-control" name="${opts.name || id}" rows="${opts.rows || 3}"
        placeholder="${_esc(opts.placeholder || '')}" ${opts.disabled ? 'disabled' : ''}>${_esc(opts.value ?? '')}</textarea>
    ` });
  }

  function select(opts = {}) {
    const id = opts.id || nextId('fc-sel');
    const optsHtml = (opts.options || []).map(o => {
      const val = typeof o === 'object' ? o.value : o;
      const lab = typeof o === 'object' ? o.label : o;
      const sel = String(val) === String(opts.value) ? 'selected' : '';
      return `<option value="${_esc(val)}" ${sel}>${_esc(lab)}</option>`;
    }).join('');
    return field({ ...opts, id, inputHtml: `
      <select id="${id}" class="form-control" name="${opts.name || id}" ${opts.disabled ? 'disabled' : ''}>${optsHtml}</select>
    ` });
  }

  function toggle(opts = {}) {
    const id = opts.id || nextId('fc-tog');
    return `
      <div class="form-group" data-field="${id}">
        <div class="toggle-wrap">
          <label class="toggle">
            <input type="checkbox" id="${id}" name="${opts.name || id}" ${opts.checked ? 'checked' : ''} ${opts.disabled ? 'disabled' : ''}>
            <span class="toggle-track"><span class="toggle-thumb"></span></span>
          </label>
          <div>
            <div class="form-label" style="margin-bottom:0;">${_esc(opts.label || '')}</div>
            ${opts.hint ? `<div class="form-hint" style="margin-top:2px;">${_esc(opts.hint)}</div>` : ''}
          </div>
        </div>
      </div>
    `;
  }

  function range(opts = {}) {
    const id = opts.id || nextId('fc-rng');
    return field({ ...opts, id, inputHtml: `
      <div style="display:flex;align-items:center;gap:var(--sp-3);">
        <input type="range" id="${id}" name="${opts.name || id}" min="${opts.min ?? 0}" max="${opts.max ?? 100}"
          step="${opts.step ?? 1}" value="${opts.value ?? 0}" style="flex:1;accent-color:var(--clr-primary);"
          oninput="this.nextElementSibling.textContent=this.value+'${_esc(opts.suffix || '')}'">
        <span class="mono text-sm" style="min-width:48px;text-align:right;">${opts.value ?? 0}${_esc(opts.suffix || '')}</span>
      </div>
    ` });
  }

  /**
   * FormBuilder — collects field markup into .form-section blocks and
   * provides serialize()/validate() against the rendered DOM.
   */
  function createBuilder(container) {
    const el = typeof container === 'string' ? document.querySelector(container) : container;
    const sections = [];

    function section(title, iconSvg, fieldsHtmlArray) {
      sections.push(`
        <div class="form-section">
          <div class="form-section-header">
            ${iconSvg ? `<span class="icon icon-md text-secondary">${iconSvg}</span>` : ''}
            <span class="form-section-title">${_esc(title)}</span>
          </div>
          <div class="form-section-body">${fieldsHtmlArray.join('')}</div>
        </div>
      `);
      return this;
    }

    function render() {
      if (el) el.innerHTML = sections.join('');
    }

    function serialize() {
      if (!el) return {};
      const data = {};
      el.querySelectorAll('input, select, textarea').forEach(inp => {
        const name = inp.name || inp.id;
        if (!name) return;
        if (inp.type === 'checkbox') data[name] = inp.checked;
        else if (inp.type === 'number' || inp.type === 'range') data[name] = parseFloat(inp.value);
        else data[name] = inp.value;
      });
      return data;
    }

    function validate(rules = {}) {
      let valid = true;
      Object.entries(rules).forEach(([name, fn]) => {
        const inp = el.querySelector(`[name="${name}"]`);
        if (!inp) return;
        const group = inp.closest('.form-group');
        const msg = fn(inp.type === 'checkbox' ? inp.checked : inp.value);
        group.classList.toggle('error', !!msg);
        inp.classList.toggle('error', !!msg);
        let errEl = group.querySelector('.form-error');
        if (msg) {
          if (!errEl) { errEl = document.createElement('div'); errEl.className = 'form-error'; group.appendChild(errEl); }
          errEl.textContent = msg;
          valid = false;
        } else if (errEl) {
          errEl.remove();
        }
      });
      return valid;
    }

    return { section, render, serialize, validate };
  }

  /* ── Shared validators ── */
  const validators = {
    required: (v) => (v === '' || v == null) ? 'This field is required.' : '',
    ipAddress: (v) => /^(\d{1,3}\.){3}\d{1,3}$/.test(v) ? '' : 'Enter a valid IP address.',
    port: (v) => (v >= 1 && v <= 65535) ? '' : 'Port must be between 1 and 65535.',
    minLength: (n) => (v) => (String(v).length >= n) ? '' : `Must be at least ${n} characters.`,
    range: (min, max) => (v) => (v >= min && v <= max) ? '' : `Must be between ${min} and ${max}.`,
  };

  function _esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  return { text, number, textarea, select, toggle, range, field, createBuilder, validators };
})();

window.FormComponents = FormComponents;
