/**
 * DWS Settings
 * Eight-section station configuration: General, Hardware, Vision,
 * Measurement, Database, Network, Appearance, Developer.
 *
 * Loads the current configuration from the backend (system_state + a
 * settings document) and writes changes back, with dirty-state tracking
 * and a sticky save bar. Appearance is applied immediately via
 * ThemeManager (client-side, no backend round-trip). Degrades gracefully
 * to locally-held defaults when the API is offline.
 *
 * Build 005.1 — Sprint 4. Reuses the pre-shipped .setting-row atoms and
 * never alters existing IDs / endpoints.
 */
'use strict';

const DWSSettings = (() => {
  const API = `http://${window.location.hostname}:8000`;

  // Schema-aligned defaults. Keys mirror system_state columns where they
  // exist (developer_mode, strict_mode, allow_near_oos, metrological_standard,
  // operator_name); the rest are station config defaults.
  const DEFAULTS = {
    // General
    station_name: 'Station #1',
    operator_name: '',
    locale: 'en-IN',
    timezone: 'Asia/Kolkata',
    // Hardware
    camera_model: 'Intel RealSense D455',
    scale_port: '/dev/ttyUSB0',
    scale_baud: '9600',
    barcode_source: 'camera',
    // Vision
    depth_fps: 30,
    depth_preset: 'high_accuracy',
    min_depth_m: 0.3,
    max_depth_m: 2.0,
    point_cloud_export: true,
    // Measurement
    metrological_standard: 'OIML',
    length_unit: 'cm',
    weight_unit: 'kg',
    strict_mode: false,
    allow_near_oos: true,
    near_oos_margin: 5,
    rounding_increment: 0.5,
    // Database
    retention_days: 365,
    auto_backup: true,
    backup_time: '02:00',
    // Network
    api_port: 8000,
    ws_port: 8765,
    mjpeg_port: 8090,
    cloud_sync: false,
    cloud_url: 'https://api.dws-cloud.example.com/v1/sync',
    // Developer
    developer_mode: false,
    verbose_logging: false,
    simulate_hardware: false,
  };

  let model = { ...DEFAULTS };
  let saved = { ...DEFAULTS };
  let dirty = false;

  /* ──────────────── INIT ──────────────── */
  function init() {
    _bindNav();
    document.getElementById('btn-settings-save')?.addEventListener('click', save);
    document.getElementById('btn-settings-discard')?.addEventListener('click', _discard);
    document.getElementById('btn-settings-reset')?.addEventListener('click', load);
    load();
  }

  function _bindNav() {
    document.querySelectorAll('.settings-nav-item').forEach(btn => {
      btn.addEventListener('click', () => _showPanel(btn.dataset.panel));
    });
  }

  function _showPanel(key) {
    document.querySelectorAll('.settings-nav-item').forEach(b => b.classList.toggle('active', b.dataset.panel === key));
    document.querySelectorAll('.settings-panel').forEach(p => p.classList.toggle('active', p.dataset.panel === key));
  }

  /* ──────────────── LOAD ──────────────── */
  async function load() {
    LoadingManager.showOverlay('Loading settings…');
    try {
      const [stateRes, settingsRes] = await Promise.allSettled([
        fetch(`${API}/system/state`, { signal: AbortSignal.timeout(5000) }),
        fetch(`${API}/settings`, { signal: AbortSignal.timeout(5000) }),
      ]);
      let incoming = {};
      if (stateRes.status === 'fulfilled' && stateRes.value.ok) {
        const s = await stateRes.value.json();
        incoming = { ...incoming, ..._pick(s, ['developer_mode', 'strict_mode', 'allow_near_oos', 'metrological_standard', 'operator_name']) };
      }
      if (settingsRes.status === 'fulfilled' && settingsRes.value.ok) {
        const s = await settingsRes.value.json();
        incoming = { ...incoming, ...s };
      }
      model = { ...DEFAULTS, ...incoming };
    } catch {
      model = { ...DEFAULTS };
      _toast('warning', 'Using defaults', 'Settings API unreachable — showing default configuration.');
    }
    // Coerce booleans that may arrive as 0/1 from SQLite.
    ['developer_mode', 'strict_mode', 'allow_near_oos', 'point_cloud_export', 'auto_backup', 'cloud_sync', 'verbose_logging', 'simulate_hardware']
      .forEach(k => { model[k] = model[k] === true || model[k] === 1 || model[k] === '1'; });
    saved = { ...model };
    LoadingManager.hideOverlay();
    _renderAll();
    _setDirty(false);
  }

  function _pick(obj, keys) {
    const out = {};
    keys.forEach(k => { if (obj[k] !== undefined && obj[k] !== null) out[k] = obj[k]; });
    return out;
  }

  /* ──────────────── RENDER ──────────────── */
  function _renderAll() {
    _panel('general', 'General', 'Station identity, operator, and locale.', [
      _rowInput('station_name', 'Station name', 'Display name shown across the HMI and on exports.'),
      _rowInput('operator_name', 'Operator name', 'Stamped onto each transaction record for this station.', 'Unassigned'),
      _rowSelect('locale', 'Locale', 'Number, date and unit formatting.', [
        { value: 'en-IN', label: 'English (India)' }, { value: 'en-US', label: 'English (US)' }, { value: 'en-GB', label: 'English (UK)' },
      ]),
      _rowSelect('timezone', 'Time zone', 'Used for timestamps and scheduled backups.', [
        'Asia/Kolkata', 'UTC', 'America/New_York', 'Europe/London', 'Asia/Singapore',
      ]),
    ]);

    _panel('hardware', 'Hardware', 'Connected camera, scale, and barcode devices.', [
      _rowSelect('camera_model', 'Depth camera', 'RealSense model providing the depth stream.', [
        'Intel RealSense D455', 'Intel RealSense D435i', 'Intel RealSense D415',
      ]),
      _rowInput('scale_port', 'Scale serial port', 'Device path of the weighing scale (Linux).'),
      _rowSelect('scale_baud', 'Scale baud rate', 'Serial baud rate for the scale.', ['9600', '19200', '38400', '115200']),
      _rowSelect('barcode_source', 'Barcode source', 'Where barcodes are decoded from.', [
        { value: 'camera', label: 'Camera (vision)' }, { value: 'usb', label: 'USB scanner' }, { value: 'serial', label: 'Serial scanner' },
      ]),
    ]);

    _panel('vision', 'Vision', 'Depth pipeline and point-cloud configuration.', [
      _rowSelect('depth_fps', 'Depth frame rate', 'Frames per second from the depth sensor.', ['15', '30', '60']),
      _rowSelect('depth_preset', 'Depth preset', 'RealSense visual preset.', [
        { value: 'high_accuracy', label: 'High Accuracy' }, { value: 'high_density', label: 'High Density' }, { value: 'default', label: 'Default' },
      ]),
      _rowNumber('min_depth_m', 'Min depth (m)', 'Nearest valid depth distance.', { step: 0.1, min: 0.1 }),
      _rowNumber('max_depth_m', 'Max depth (m)', 'Furthest valid depth distance.', { step: 0.1, min: 0.5 }),
      _rowToggle('point_cloud_export', 'Export point clouds', 'Save a .ply point cloud per locked transaction.'),
    ]);

    _panel('measurement', 'Measurement', 'Metrology rules and tolerances.', [
      _rowSelect('metrological_standard', 'Metrological standard', 'Governing standard for legal-for-trade measurement.', ['OIML', 'NTEP', 'NMI']),
      _rowSelect('length_unit', 'Length unit', 'Unit for dimension display.', ['cm', 'mm', 'in']),
      _rowSelect('weight_unit', 'Weight unit', 'Unit for weight display.', ['kg', 'g', 'lb']),
      _rowNumber('rounding_increment', 'Rounding increment', 'Smallest dimension increment (in length unit).', { step: 0.1, min: 0.1 }),
      _rowToggle('strict_mode', 'Strict mode', 'Reject any parcel whose measurement is uncertain.'),
      _rowToggle('allow_near_oos', 'Allow near out-of-spec', 'Accept parcels just inside tolerance with a warning.'),
      _rowNumber('near_oos_margin', 'Near-OOS margin (%)', 'Margin treated as “near” out-of-spec.', { step: 1, min: 0, max: 50 }),
    ]);

    _panel('database', 'Database', 'Local SQLite retention and backups.', [
      _rowNumber('retention_days', 'Retention (days)', 'How long transactions are kept before pruning. 0 = never prune.', { step: 1, min: 0 }),
      _rowToggle('auto_backup', 'Automatic backups', 'Create a scheduled database backup each day.'),
      _rowInput('backup_time', 'Backup time', 'Local time for the daily backup (HH:MM).', '', 'time'),
      _rowButton('Backup now', 'Create an on-demand backup of the database.', 'btn-db-backup', 'Backup'),
    ]);

    _panel('network', 'Network', 'Service ports and optional cloud sync.', [
      _rowNumber('api_port', 'REST API port', 'FastAPI server port.', { step: 1, min: 1, max: 65535 }),
      _rowNumber('ws_port', 'WebSocket port', 'Live HMI bridge port.', { step: 1, min: 1, max: 65535 }),
      _rowNumber('mjpeg_port', 'MJPEG port', 'Live camera stream port.', { step: 1, min: 1, max: 65535 }),
      _rowToggle('cloud_sync', 'Cloud sync', 'Push unsynced records to the fleet backend.'),
      _rowInput('cloud_url', 'Cloud sync URL', 'Endpoint used when cloud sync is enabled.'),
    ]);

    _renderAppearance();

    _panel('developer', 'Developer', 'Diagnostics and development aids. Disable on production stations.', [
      _rowToggle('developer_mode', 'Developer mode', 'Enables developer-only panels and relaxes hardware checks.'),
      _rowToggle('verbose_logging', 'Verbose logging', 'Write detailed debug logs to ~/.dws/logs.'),
      _rowToggle('simulate_hardware', 'Simulate hardware', 'Run the SimBus pipeline without physical devices.'),
      _rowButton('Open logs folder', 'Reveal the application logs directory.', 'btn-open-logs', 'Open'),
    ]);

    _bindControls();
  }

  function _panel(key, title, sub, rowsHtml) {
    const el = document.querySelector(`.settings-panel[data-panel="${key}"]`);
    if (!el) return;
    el.innerHTML = `
      <div class="settings-panel-head">
        <div class="settings-panel-title">${title}</div>
        <div class="settings-panel-sub">${sub}</div>
      </div>
      <div class="form-section">
        <div class="form-section-body settings-rows">${rowsHtml.join('')}</div>
      </div>`;
  }

  function _renderAppearance() {
    const el = document.querySelector('.settings-panel[data-panel="appearance"]');
    if (!el) return;
    const theme = ThemeManager.getTheme();
    const accent = ThemeManager.getAccent();
    const accentColors = { blue: '#1a8cff', teal: '#00d4aa', amber: '#f59e0b', violet: '#8b5cf6' };
    el.innerHTML = `
      <div class="settings-panel-head">
        <div class="settings-panel-title">Appearance</div>
        <div class="settings-panel-sub">Theme and accent colour. Applied immediately and saved to this device.</div>
      </div>
      <div class="form-section">
        <div class="form-section-body settings-rows">
          <div class="setting-row">
            <div class="setting-row-text">
              <div class="setting-row-label">Contrast theme</div>
              <div class="setting-row-desc">Switch between the standard dark theme and a higher-contrast variant.</div>
            </div>
            <div class="setting-row-control">
              <select class="form-control" id="appearance-theme">
                <option value="dark" ${theme === 'dark' ? 'selected' : ''}>Dark (standard)</option>
                <option value="dark-high" ${theme === 'dark-high' ? 'selected' : ''}>Dark (high contrast)</option>
              </select>
            </div>
          </div>
          <div class="setting-row">
            <div class="setting-row-text">
              <div class="setting-row-label">Accent colour</div>
              <div class="setting-row-desc">Primary colour used for highlights, charts, and active states.</div>
            </div>
            <div class="setting-row-control" style="min-width:auto;">
              <div class="accent-swatches" id="accent-swatches">
                ${ThemeManager.getAccents().map(a => `
                  <button type="button" class="accent-swatch ${a === accent ? 'selected' : ''}" data-accent="${a}"
                    style="background:${accentColors[a] || '#1a8cff'};" aria-label="${a} accent" title="${a}"></button>`).join('')}
              </div>
            </div>
          </div>
        </div>
      </div>`;
  }

  /* ── Row builders (setting-row layout) ── */
  function _rowWrap(label, desc, controlHtml) {
    return `
      <div class="setting-row">
        <div class="setting-row-text">
          <div class="setting-row-label">${_esc(label)}</div>
          ${desc ? `<div class="setting-row-desc">${_esc(desc)}</div>` : ''}
        </div>
        <div class="setting-row-control">${controlHtml}</div>
      </div>`;
  }
  function _rowInput(key, label, desc, placeholder = '', type = 'text') {
    return _rowWrap(label, desc, `<input class="form-control" id="set-${key}" type="${type}" value="${_esc(model[key] ?? '')}" placeholder="${_esc(placeholder)}">`);
  }
  function _rowNumber(key, label, desc, { step = 'any', min = '', max = '' } = {}) {
    return _rowWrap(label, desc, `<input class="form-control" id="set-${key}" type="number" value="${model[key] ?? ''}" step="${step}" min="${min}" max="${max}">`);
  }
  function _rowSelect(key, label, desc, options) {
    const opts = options.map(o => {
      const v = typeof o === 'object' ? o.value : o;
      const l = typeof o === 'object' ? o.label : o;
      return `<option value="${_esc(v)}" ${String(v) === String(model[key]) ? 'selected' : ''}>${_esc(l)}</option>`;
    }).join('');
    return _rowWrap(label, desc, `<select class="form-control" id="set-${key}">${opts}</select>`);
  }
  function _rowToggle(key, label, desc) {
    return _rowWrap(label, desc, `
      <label class="toggle">
        <input type="checkbox" id="set-${key}" ${model[key] ? 'checked' : ''}>
        <span class="toggle-track"><span class="toggle-thumb"></span></span>
      </label>`);
  }
  function _rowButton(label, desc, id, btnLabel) {
    return _rowWrap(label, desc, `<button class="btn btn-secondary" id="${id}" type="button">${_esc(btnLabel)}</button>`);
  }

  /* ──────────────── BIND ──────────────── */
  function _bindControls() {
    Object.keys(DEFAULTS).forEach(key => {
      const el = document.getElementById(`set-${key}`);
      if (!el) return;
      const evt = el.type === 'checkbox' ? 'change' : 'input';
      el.addEventListener(evt, () => {
        model[key] = el.type === 'checkbox' ? el.checked
          : el.type === 'number' ? (el.value === '' ? '' : parseFloat(el.value))
          : el.value;
        _recomputeDirty();
      });
    });

    // Appearance (immediate, device-local)
    document.getElementById('appearance-theme')?.addEventListener('change', e => {
      ThemeManager.setTheme(e.target.value);
      _toast('info', 'Theme updated', 'Contrast theme applied to this device.');
    });
    document.querySelectorAll('#accent-swatches .accent-swatch').forEach(sw => {
      sw.addEventListener('click', () => {
        ThemeManager.setAccent(sw.dataset.accent);
        document.querySelectorAll('#accent-swatches .accent-swatch').forEach(s => s.classList.toggle('selected', s === sw));
      });
    });

    // Action buttons
    document.getElementById('btn-db-backup')?.addEventListener('click', _backupNow);
    document.getElementById('btn-open-logs')?.addEventListener('click', _openLogs);
  }

  function _recomputeDirty() {
    const changed = Object.keys(DEFAULTS).some(k => String(model[k]) !== String(saved[k]));
    _setDirty(changed);
  }

  function _setDirty(on) {
    dirty = on;
    const text = document.getElementById('savebar-text');
    const save = document.getElementById('btn-settings-save');
    const discard = document.getElementById('btn-settings-discard');
    if (text) { text.textContent = on ? 'You have unsaved changes' : 'All changes saved'; text.classList.toggle('dirty', on); }
    if (save) save.disabled = !on;
    if (discard) discard.disabled = !on;
  }

  /* ──────────────── SAVE / DISCARD ──────────────── */
  async function save() {
    LoadingManager.setButtonLoading('#btn-settings-save', true);
    const sess = _session();
    let ok = false;
    try {
      const r = await fetch(`${API}/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(sess.token ? { Authorization: `Bearer ${sess.token}` } : {}) },
        body: JSON.stringify(model),
        signal: AbortSignal.timeout(8000),
      });
      ok = r.ok;
    } catch { ok = false; }
    LoadingManager.setButtonLoading('#btn-settings-save', false);

    if (ok) _toast('success', 'Settings saved', 'Configuration applied to the station.');
    else _toast('warning', 'Saved locally', 'Backend unavailable — changes kept in this session only.');

    saved = { ...model };
    _setDirty(false);
  }

  function _discard() {
    model = { ...saved };
    _renderAll();
    _setDirty(false);
    _toast('info', 'Changes discarded', 'Reverted to the last saved configuration.');
  }

  async function _backupNow() {
    LoadingManager.setButtonLoading('#btn-db-backup', true);
    try {
      const r = await fetch(`${API}/backup`, { method: 'POST', signal: AbortSignal.timeout(15000) });
      _toast(r.ok ? 'success' : 'error', r.ok ? 'Backup created' : 'Backup failed', r.ok ? 'A database backup was written successfully.' : 'The backend could not complete the backup.');
    } catch { _toast('error', 'Backup failed', 'Backend unreachable.'); }
    LoadingManager.setButtonLoading('#btn-db-backup', false);
  }

  function _openLogs() {
    if (window.dws?.openLogs) { window.dws.openLogs(); _toast('info', 'Opening logs', 'Logs folder opened in the file manager.'); }
    else _toast('info', 'Logs location', '~/.dws/logs — available from the desktop application.');
  }

  function _toast(type, title, msg) {
    if (window.ToastManager) (ToastManager[type] || ToastManager.info)(title, msg);
    else if (window.DWS_UI) (DWS_UI.Toast[type] || DWS_UI.Toast.info)(title, msg);
  }
  function _session() { try { return JSON.parse(sessionStorage.getItem('dws_session') || '{}'); } catch { return {}; } }
  function _esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }

  return { init };
})();

window.DWSSettings = DWSSettings;
