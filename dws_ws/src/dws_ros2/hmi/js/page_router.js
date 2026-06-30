/**
 * DWS Page Router
 * Thin lifecycle layer on top of DWS_UI.Navigation (ui_manager.js) /
 * DWSNav (navigation.js). Two responsibilities:
 *
 *  1. Multi-page mode (current default): registers an auth guard and a
 *     beforeunload-style destroy hook so each page's *.js module can
 *     clean up timers/polling without every page re-implementing it.
 *
 *  2. Optional SPA mode: if a page defines `[data-section]` blocks (the
 *     pattern Navigation.navigateTo already understands), PageRouter
 *     drives section switching via hash changes and calls the matching
 *     registered lifecycle's init()/destroy().
 *
 * Build 005.1 — Sprint 3
 */
'use strict';

const PageRouter = (() => {
  const registry = {};   // pageKey -> { init, destroy }
  let current = null;

  function register(pageKey, lifecycle) {
    registry[pageKey] = lifecycle;
  }

  function _detectPage() {
    const hash = window.location.hash.replace('#', '');
    if (hash) return hash;
    return window.location.pathname.split('/').pop().replace('.html', '') || 'dashboard';
  }

  /** Call once per page on DOMContentLoaded for multi-page mode. */
  function boot(pageKey) {
    pageKey = pageKey || _detectPage();
    current = pageKey;

    if (window.DWSNav) window.DWSNav.init(pageKey);
    else if (window.DWS_UI) window.DWS_UI.Navigation.init();

    const lc = registry[pageKey];
    if (lc && lc.init) lc.init();

    window.addEventListener('beforeunload', () => {
      if (lc && lc.destroy) lc.destroy();
    });

    window.addEventListener('hashchange', () => {
      const sections = document.querySelectorAll('[data-section]');
      if (!sections.length) return; // multi-page mode, ignore
      _switchSection(_detectPage());
    });
  }

  function _switchSection(pageKey) {
    if (current && registry[current] && registry[current].destroy) {
      registry[current].destroy();
    }
    current = pageKey;
    const lc = registry[pageKey];
    if (lc && lc.init) lc.init();
  }

  function navigate(pageKey) {
    if (window.DWSNav) {
      window.location.href = pageKey + '.html';
    } else if (window.DWS_UI) {
      window.DWS_UI.Navigation.navigateTo(pageKey);
    } else {
      window.location.hash = pageKey;
    }
  }

  /** Auth guard — redirect to login.html if no session token present. */
  function requireAuth() {
    try {
      const sess = JSON.parse(sessionStorage.getItem('dws_session') || '{}');
      if (!sess.token) { window.location.href = 'login.html'; return null; }
      return sess;
    } catch {
      window.location.href = 'login.html';
      return null;
    }
  }

  /** Permission guard — relies on session.role + AUTHENTICATION_ARCHITECTURE.md's ROLE_PERMISSIONS map. */
  const ROLE_PERMISSIONS = {
    operator:      ['dashboard.view', 'live_scan.view', 'transactions.view'],
    supervisor:    ['dashboard.view', 'live_scan.view', 'transactions.view', 'reports.view', 'diagnostics.view', 'export.run'],
    administrator: ['dashboard.view', 'live_scan.view', 'transactions.view', 'reports.view', 'diagnostics.view', 'export.run', 'settings.write', 'calibration.run', 'users.manage', 'roles.manage'],
  };

  function hasPermission(role, permission) {
    if (role === 'administrator') return true; // fail-open, mirrors permissions.py
    return (ROLE_PERMISSIONS[role] || []).includes(permission);
  }

  function requirePermission(permission, redirectTo = 'dashboard.html') {
    const sess = requireAuth();
    if (!sess) return null;
    if (!hasPermission(sess.role || 'operator', permission)) {
      if (window.ToastManager) {
        ToastManager.error('Access restricted', `Your role (${sess.role}) does not have access to this page.`);
      }
      setTimeout(() => { window.location.href = redirectTo; }, 1200);
      return null;
    }
    return sess;
  }

  return { register, boot, navigate, requireAuth, requirePermission, hasPermission };
})();

window.PageRouter = PageRouter;
