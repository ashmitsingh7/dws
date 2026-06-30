/**
 * DWS Navigation Module
 * Injects the shared sidebar + topbar into every page, manages active state,
 * breadcrumbs, and page-section switching for both SPA and multi-page modes.
 * Build 005.1  — Sprint 2
 *
 * Does NOT modify any IDs/contracts used by ui_manager.js
 */
'use strict';

/* ============================================================
   SIDEBAR HTML TEMPLATE
   ============================================================ */
const SIDEBAR_HTML = `
<aside class="sidebar" id="dws-sidebar">
  <div class="sidebar-brand">
    <div class="brand-icon">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
        <polyline points="3.27 6.96 12 12.01 20.73 6.96"/>
        <line x1="12" y1="22.08" x2="12" y2="12"/>
      </svg>
    </div>
    <div class="brand-text">
      <span class="brand-name">DWS</span>
      <span class="brand-sub">Delhivery v1.0</span>
    </div>
  </div>

  <nav class="sidebar-nav" aria-label="Main navigation">
    <div class="nav-section">
      <div class="nav-section-label">Operations</div>
      <a class="nav-item" data-page="dashboard" href="dashboard.html" data-tooltip="Dashboard">
        <span class="nav-item-icon"><svg class="icon icon-md"><use href="#icon-dashboard"/></svg></span>
        <span class="nav-item-label">Dashboard</span>
      </a>
      <a class="nav-item" data-page="live" href="live.html" data-tooltip="Live Scan">
        <span class="nav-item-icon"><svg class="icon icon-md"><use href="#icon-camera"/></svg></span>
        <span class="nav-item-label">Live Scan</span>
      </a>
      <a class="nav-item" data-page="transactions" href="transactions.html" data-tooltip="Transactions">
        <span class="nav-item-icon"><svg class="icon icon-md"><use href="#icon-transactions"/></svg></span>
        <span class="nav-item-label">Transactions</span>
      </a>
      <a class="nav-item" data-page="reports" href="reports.html" data-tooltip="Reports">
        <span class="nav-item-icon"><svg class="icon icon-md"><use href="#icon-reports"/></svg></span>
        <span class="nav-item-label">Reports</span>
      </a>
    </div>

    <div class="nav-section">
      <div class="nav-section-label">System</div>
      <a class="nav-item" data-page="diagnostics" href="diagnostics.html" data-tooltip="Diagnostics">
        <span class="nav-item-icon"><svg class="icon icon-md"><use href="#icon-diagnostics"/></svg></span>
        <span class="nav-item-label">Diagnostics</span>
      </a>
      <a class="nav-item" data-page="logs" href="logs.html" data-tooltip="Audit Logs">
        <span class="nav-item-icon"><svg class="icon icon-md"><use href="#icon-logs"/></svg></span>
        <span class="nav-item-label">Audit Logs</span>
      </a>
      <a class="nav-item" data-page="calibration" href="calibration.html" data-tooltip="Calibration">
        <span class="nav-item-icon"><svg class="icon icon-md"><use href="#icon-calibration"/></svg></span>
        <span class="nav-item-label">Calibration</span>
      </a>
      <a class="nav-item" data-page="settings" href="settings.html" data-tooltip="Settings">
        <span class="nav-item-icon"><svg class="icon icon-md"><use href="#icon-settings"/></svg></span>
        <span class="nav-item-label">Settings</span>
      </a>
    </div>

    <div class="nav-section">
      <div class="nav-section-label">Admin</div>
      <a class="nav-item" data-page="users" href="users.html" data-tooltip="User Management">
        <span class="nav-item-icon"><svg class="icon icon-md"><use href="#icon-users"/></svg></span>
        <span class="nav-item-label">User Management</span>
      </a>
      <a class="nav-item" data-page="about" href="about.html" data-tooltip="About">
        <span class="nav-item-icon"><svg class="icon icon-md"><use href="#icon-about"/></svg></span>
        <span class="nav-item-label">About</span>
      </a>
    </div>
  </nav>

  <div class="sidebar-footer">
    <div class="sidebar-user" id="sidebar-user-area" role="button" tabindex="0">
      <div class="sidebar-user-avatar" id="sidebar-avatar">OP</div>
      <div class="sidebar-user-info">
        <div class="sidebar-user-name" id="sidebar-username">Operator</div>
        <div class="sidebar-user-role" id="sidebar-userrole">operator</div>
      </div>
    </div>
  </div>
</aside>
`;

const TOPBAR_HTML = (pageTitle = 'Dashboard') => `
<header class="topbar" id="dws-topbar">
  <button class="topbar-collapse-btn" aria-label="Toggle sidebar">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <line x1="3" y1="12" x2="21" y2="12"/>
      <line x1="3" y1="6"  x2="21" y2="6"/>
      <line x1="3" y1="18" x2="21" y2="18"/>
    </svg>
  </button>

  <div class="topbar-breadcrumb">
    <span>DWS</span>
    <span class="bc-sep">›</span>
    <span class="bc-current" id="bc-current">${pageTitle}</span>
  </div>

  <div class="topbar-actions">
    <div class="topbar-health-dot healthy" id="health-dot" title="System: healthy"></div>
    <span class="topbar-time" id="topbar-clock">--:--:--</span>
  </div>
</header>
`;

/* ============================================================
   NAV INJECTOR
   Renders sidebar + topbar into .app-shell if they aren't already present.
   Call DWSNav.init() at the top of each page's <script>.
   ============================================================ */
const DWSNav = (() => {

  function init(pageKey) {
    const shell = document.querySelector('.app-shell');
    if (!shell) return;

    // Inject icon sprite if not already in DOM
    if (!document.getElementById('dws-icons-sprite')) {
      const div = document.createElement('div');
      div.id = 'dws-icons-sprite';
      div.setAttribute('aria-hidden', 'true');
      div.style.cssText = 'position:absolute;width:0;height:0;overflow:hidden;';
      document.body.insertAdjacentElement('afterbegin', div);
      // Icons are expected to be inlined in the page or loaded via fetch
      _loadIconSprite(div);
    }

    // Inject sidebar if missing
    if (!shell.querySelector('.sidebar')) {
      const tmp = document.createElement('div');
      tmp.innerHTML = SIDEBAR_HTML.trim();
      shell.insertAdjacentElement('afterbegin', tmp.firstElementChild);
    }

    // Inject topbar if missing
    if (!shell.querySelector('.topbar')) {
      const pageTitle = _pageName(pageKey || _detectPage());
      const tmp = document.createElement('div');
      tmp.innerHTML = TOPBAR_HTML(pageTitle).trim();
      const sidebar = shell.querySelector('.sidebar');
      sidebar
        ? sidebar.insertAdjacentElement('afterend', tmp.firstElementChild)
        : shell.insertAdjacentElement('afterbegin', tmp.firstElementChild);
    }

    _markActive(pageKey || _detectPage());
    _loadUserInfo();
  }

  function _loadIconSprite(container) {
    // Try to fetch icons.svg (co-located) and inject
    fetch('icons.svg')
      .then(r => r.text())
      .then(html => { container.innerHTML = html; })
      .catch(() => {}); // icons not critical for layout
  }

  function _detectPage() {
    const hash = window.location.hash.replace('#', '');
    if (hash) return hash;
    return window.location.pathname.split('/').pop().replace('.html', '') || 'dashboard';
  }

  function _markActive(page) {
    document.querySelectorAll('.nav-item[data-page]').forEach(item => {
      item.classList.toggle('active', item.getAttribute('data-page') === page);
    });
    const bc = document.getElementById('bc-current');
    if (bc) bc.textContent = _pageName(page);
  }

  function _pageName(page) {
    const names = {
      dashboard: 'Dashboard', live: 'Live Measurement',
      transactions: 'Transactions', reports: 'Reports',
      diagnostics: 'Diagnostics', calibration: 'Calibration',
      settings: 'Settings', users: 'User Management',
      about: 'About', logs: 'System Logs',
    };
    return names[page] || page.charAt(0).toUpperCase() + page.slice(1);
  }

  function _loadUserInfo() {
    try {
      const sess = JSON.parse(sessionStorage.getItem('dws_session') || '{}');
      const av  = document.getElementById('sidebar-avatar');
      const nm  = document.getElementById('sidebar-username');
      const rl  = document.getElementById('sidebar-userrole');
      if (!av || !nm || !rl) return;
      const name = sess.username || 'Operator';
      const role = sess.role     || 'operator';
      nm.textContent = name;
      rl.textContent = role;
      av.textContent = name.slice(0, 2).toUpperCase();
    } catch { /* session not available */ }
  }

  function setPage(page) {
    _markActive(page);
  }

  return { init, setPage };
})();

window.DWSNav = DWSNav;
