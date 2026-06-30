/**
 * DWS Shell
 * Cross-page chrome that layers on top of the navigation injected by
 * navigation.js without changing any existing IDs or contracts:
 *
 *   - Topbar user-profile dropdown (profile, settings, shortcuts, sign out)
 *   - Notification center slide-over (sourced from real /transactions events)
 *   - Global keyboard shortcuts + a discoverable shortcuts overlay
 *   - Single sign-out path that clears the session and returns to login
 *
 * Call DWSShell.init(pageKey) after DWSNav.init(pageKey).
 * Build 005.1 — Sprint 4
 */
'use strict';

const DWSShell = (() => {
  const API = () => `http://${window.location.hostname}:8000`;
  let pendingNav = false;
  let notifTimer = null;

  function init() {
    _enhanceTopbar();
    _buildProfileMenu();
    _buildNotifications();
    _buildShortcutsOverlay();
    _bindSidebarUser();
    _bindShortcuts();
    _refreshNotifications();
    notifTimer = setInterval(_refreshNotifications, 30000);
    window.addEventListener('beforeunload', () => { if (notifTimer) clearInterval(notifTimer); });
  }

  /* ──────────────── TOPBAR ──────────────── */
  function _enhanceTopbar() {
    const actions = document.querySelector('.topbar-actions');
    if (!actions || actions.dataset.enhanced) return;
    actions.dataset.enhanced = '1';

    // Notification bell (before the health dot)
    const bell = document.createElement('button');
    bell.className = 'topbar-icon-btn';
    bell.id = 'topbar-bell';
    bell.setAttribute('aria-label', 'Notifications');
    bell.innerHTML = `<svg class="icon icon-md"><use href="#icon-bell"/></svg><span class="notif-count" id="notif-count"></span>`;
    actions.insertBefore(bell, actions.firstChild);

    // Profile trigger (at the end)
    const sess = _session();
    const name = sess.username || 'Operator';
    const wrap = document.createElement('div');
    wrap.style.position = 'relative';
    wrap.innerHTML = `
      <button class="profile-trigger" id="profile-trigger" aria-haspopup="menu" aria-expanded="false">
        <span class="profile-avatar" id="profile-avatar">${_initials(name)}</span>
        <span class="profile-name">${_esc(name)}</span>
        <svg class="icon icon-sm" style="color:var(--clr-text-tertiary)"><use href="#icon-chevron-right"/></svg>
      </button>`;
    actions.appendChild(wrap);
    wrap.querySelector('svg use')?.setAttribute('href', '#icon-chevron-right');
  }

  /* ──────────────── PROFILE MENU ──────────────── */
  function _buildProfileMenu() {
    const trigger = document.getElementById('profile-trigger');
    if (!trigger) return;
    const sess = _session();
    const role = sess.role || 'operator';
    const canSettings = !window.PageRouter || PageRouter.hasPermission(role, 'settings.write');
    const canUsers = !window.PageRouter || PageRouter.hasPermission(role, 'users.manage');

    const menu = document.createElement('div');
    menu.className = 'menu-dropdown';
    menu.id = 'profile-menu';
    menu.setAttribute('role', 'menu');
    menu.innerHTML = `
      <div class="menu-dropdown-header">
        <div class="menu-dropdown-name">${_esc(sess.username || 'Operator')}</div>
        <div class="menu-dropdown-meta"><span class="role-badge ${role}">${_esc(role)}</span></div>
      </div>
      <a class="menu-item" href="settings.html" ${canSettings ? '' : 'style="display:none"'}><span class="icon icon-md"><svg><use href="#icon-settings"/></svg></span> Settings</a>
      <a class="menu-item" href="users.html" ${canUsers ? '' : 'style="display:none"'}><span class="icon icon-md"><svg><use href="#icon-users"/></svg></span> User Management</a>
      <a class="menu-item" href="about.html"><span class="icon icon-md"><svg><use href="#icon-about"/></svg></span> About &amp; System Info</a>
      <button class="menu-item" id="menu-shortcuts"><span class="icon icon-md"><svg><use href="#icon-layers"/></svg></span> Keyboard Shortcuts <span class="menu-kbd">?</span></button>
      <div class="menu-divider"></div>
      <button class="menu-item danger" id="menu-logout"><span class="icon icon-md"><svg><use href="#icon-logout"/></svg></span> Sign Out</button>
    `;
    trigger.parentElement.appendChild(menu);

    trigger.addEventListener('click', (e) => { e.stopPropagation(); _toggleMenu(menu, trigger); });
    document.addEventListener('click', (e) => { if (!menu.contains(e.target) && e.target !== trigger) _closeMenu(menu, trigger); });
    menu.querySelector('#menu-logout').addEventListener('click', logout);
    menu.querySelector('#menu-shortcuts').addEventListener('click', () => { _closeMenu(menu, trigger); toggleShortcuts(); });
  }

  function _toggleMenu(menu, trigger) {
    const open = menu.classList.toggle('open');
    trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  function _closeMenu(menu, trigger) {
    menu.classList.remove('open');
    trigger.setAttribute('aria-expanded', 'false');
  }

  function _bindSidebarUser() {
    const area = document.getElementById('sidebar-user-area');
    if (!area) return;
    area.addEventListener('click', () => {
      const t = document.getElementById('profile-trigger');
      const m = document.getElementById('profile-menu');
      if (t && m) _toggleMenu(m, t);
    });
  }

  /* ──────────────── NOTIFICATIONS ──────────────── */
  function _buildNotifications() {
    const scrim = document.createElement('div');
    scrim.className = 'scrim';
    scrim.id = 'notif-scrim';
    document.body.appendChild(scrim);

    const panel = document.createElement('aside');
    panel.className = 'notif-panel';
    panel.id = 'notif-panel';
    panel.setAttribute('aria-label', 'Notification center');
    panel.innerHTML = `
      <div class="notif-panel-header">
        <span class="notif-panel-title">Notifications</span>
        <div class="row">
          <button class="btn btn-ghost btn-sm" id="notif-mark-read">Mark all read</button>
          <button class="topbar-icon-btn" id="notif-close" aria-label="Close"><svg class="icon icon-md"><use href="#icon-x"/></svg></button>
        </div>
      </div>
      <div class="notif-panel-body" id="notif-body"></div>
    `;
    document.body.appendChild(panel);

    const bell = document.getElementById('topbar-bell');
    if (bell) bell.addEventListener('click', () => _openNotifs(true));
    scrim.addEventListener('click', () => _openNotifs(false));
    panel.querySelector('#notif-close').addEventListener('click', () => _openNotifs(false));
    panel.querySelector('#notif-mark-read').addEventListener('click', () => {
      panel.querySelectorAll('.notif-item.unread').forEach(n => n.classList.remove('unread'));
      _setCount(0);
    });
  }

  function _openNotifs(open) {
    document.getElementById('notif-panel')?.classList.toggle('open', open);
    document.getElementById('notif-scrim')?.classList.toggle('open', open);
  }

  async function _refreshNotifications() {
    const body = document.getElementById('notif-body');
    if (!body) return;
    try {
      const r = await fetch(`${API()}/transactions?limit=50`, { signal: AbortSignal.timeout(5000) });
      if (!r.ok) throw new Error('http');
      const d = await r.json();
      const list = Array.isArray(d) ? d : (d.transactions || []);
      const events = list
        .filter(t => t.status === 'error' || t.status === 'failed' || t.status === 'warning' || t.status === 'near_oos')
        .slice(0, 15);
      if (!events.length) {
        body.innerHTML = `<div class="empty-state" style="padding:var(--sp-12) var(--sp-6);"><div class="empty-state-icon"><svg class="icon icon-lg"><use href="#icon-check"/></svg></div><div class="empty-state-title">All clear</div><div class="empty-state-sub">No alerts. The system is operating nominally.</div></div>`;
        _setCount(0);
        return;
      }
      body.innerHTML = events.map(e => {
        const crit = e.status === 'error' || e.status === 'failed';
        const title = crit ? 'Scan error' : 'Near out-of-spec';
        const text = e.error_message || `Barcode ${e.barcode || 'unknown'} — ${e.status}`;
        const ts = e.timestamp ? new Date(e.timestamp).toLocaleString() : '';
        return `<div class="notif-item unread">
          <span class="notif-dot" style="${crit ? 'background:var(--clr-danger);box-shadow:0 0 6px var(--clr-danger)' : 'background:var(--clr-warning)'}"></span>
          <div><div class="notif-title">${title}</div><div class="notif-text">${_esc(text)}</div><div class="notif-time">${ts}</div></div>
        </div>`;
      }).join('');
      _setCount(events.length);
    } catch {
      body.innerHTML = `<div class="empty-state" style="padding:var(--sp-12) var(--sp-6);"><div class="empty-state-sub">Notifications unavailable — backend offline.</div></div>`;
      _setCount(0);
    }
  }

  function _setCount(n) {
    const el = document.getElementById('notif-count');
    if (el) el.textContent = n > 0 ? (n > 99 ? '99+' : String(n)) : '';
  }

  /* ──────────────── KEYBOARD SHORTCUTS ──────────────── */
  function _buildShortcutsOverlay() {
    if (document.getElementById('shortcuts-overlay')) return;
    const groups = [
      ['Go to Dashboard', 'G then D'], ['Go to Live Scan', 'G then L'],
      ['Go to Transactions', 'G then T'], ['Go to Reports', 'G then R'],
      ['Go to Diagnostics', 'G then I'], ['Go to Calibration', 'G then C'],
      ['Go to Settings', 'G then S'], ['Open notifications', 'N'],
      ['Toggle sidebar', '['], ['Focus search', '/'],
      ['Show this help', '?'], ['Close dialogs', 'Esc'],
    ];
    const el = document.createElement('div');
    el.className = 'modal-overlay';
    el.id = 'shortcuts-overlay';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-label', 'Keyboard shortcuts');
    el.innerHTML = `
      <div class="modal" style="max-width:600px">
        <div class="modal-header">
          <span class="icon icon-md text-secondary"><svg><use href="#icon-layers"/></svg></span>
          <h3 class="modal-title">Keyboard Shortcuts</h3>
          <button class="modal-close" id="shortcuts-close" aria-label="Close"><svg class="icon icon-md"><use href="#icon-x"/></svg></button>
        </div>
        <div class="modal-body">
          <div class="kbd-overlay-grid">
            ${groups.map(([label, key]) => `<div class="kbd-row"><span>${label}</span><span class="kbd">${key}</span></div>`).join('')}
          </div>
        </div>
      </div>`;
    document.body.appendChild(el);
    el.querySelector('#shortcuts-close').addEventListener('click', () => toggleShortcuts(false));
    el.addEventListener('click', (e) => { if (e.target === el) toggleShortcuts(false); });
  }

  function toggleShortcuts(force) {
    const el = document.getElementById('shortcuts-overlay');
    if (!el) return;
    const open = force == null ? !el.classList.contains('open') : force;
    el.classList.toggle('open', open);
  }

  function _bindShortcuts() {
    document.addEventListener('keydown', (e) => {
      const tag = (e.target.tagName || '').toLowerCase();
      const typing = tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable;

      if (e.key === 'Escape') {
        toggleShortcuts(false);
        _openNotifs(false);
        document.getElementById('profile-menu')?.classList.remove('open');
        return;
      }
      if (typing) return;

      if (e.key === '?' || (e.shiftKey && e.key === '/')) { e.preventDefault(); toggleShortcuts(); return; }
      if (e.key === '/') {
        const search = document.querySelector('.table-search input, input[type="search"]');
        if (search) { e.preventDefault(); search.focus(); }
        return;
      }
      if (e.key === 'n' || e.key === 'N') { _openNotifs(!document.getElementById('notif-panel')?.classList.contains('open')); return; }
      if (e.key === '[') {
        if (window.DWS_UI?.Navigation) DWS_UI.Navigation.toggleSidebar();
        return;
      }

      // "g" then destination
      if (e.key === 'g' || e.key === 'G') { pendingNav = true; setTimeout(() => { pendingNav = false; }, 1200); return; }
      if (pendingNav) {
        const map = { d: 'dashboard', l: 'live', t: 'transactions', r: 'reports', i: 'diagnostics', c: 'calibration', s: 'settings', u: 'users', a: 'about' };
        const dest = map[e.key.toLowerCase()];
        pendingNav = false;
        if (dest) { window.location.href = dest + '.html'; }
      }
    });
  }

  /* ──────────────── SESSION ──────────────── */
  function logout() {
    try {
      const sess = _session();
      if (sess.token) {
        // Best-effort server-side revoke; never block the client logout on it.
        fetch(`${API()}/auth/logout`, { method: 'POST', headers: { Authorization: `Bearer ${sess.token}` } }).catch(() => {});
      }
    } catch {}
    sessionStorage.removeItem('dws_session');
    window.location.href = 'login.html';
  }

  function _session() {
    try { return JSON.parse(sessionStorage.getItem('dws_session') || '{}'); } catch { return {}; }
  }
  function _initials(name) { return String(name).slice(0, 2).toUpperCase(); }
  function _esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

  return { init, logout, toggleShortcuts, refreshNotifications: _refreshNotifications };
})();

window.DWSShell = DWSShell;
