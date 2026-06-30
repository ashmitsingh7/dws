/**
 * DWS Theme Manager
 * Manages theme preferences (dark/light contrast toggle), accent colour
 * overrides, and persists choices to localStorage.
 * Build 005.1 — Sprint 2
 */
'use strict';

const ThemeManager = (() => {
  const KEY_THEME   = 'dws_theme';
  const KEY_ACCENT  = 'dws_accent';

  const ACCENTS = {
    blue:  { primary: '#1a8cff', dim: '#1265c4', glow: 'rgba(26,140,255,0.18)' },
    teal:  { primary: '#00d4aa', dim: '#00a887', glow: 'rgba(0,212,170,0.18)' },
    amber: { primary: '#f59e0b', dim: '#d97706', glow: 'rgba(245,158,11,0.18)' },
    violet:{ primary: '#8b5cf6', dim: '#7c3aed', glow: 'rgba(139,92,246,0.18)' },
  };

  function init() {
    const saved = localStorage.getItem(KEY_THEME) || 'dark';
    _applyTheme(saved);
    const accent = localStorage.getItem(KEY_ACCENT) || 'blue';
    _applyAccent(accent);
  }

  function _applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    if (theme === 'dark-high') {
      // Higher contrast override
      document.documentElement.style.setProperty('--clr-bg-base', '#000000');
      document.documentElement.style.setProperty('--clr-text-primary', '#ffffff');
    } else {
      document.documentElement.style.removeProperty('--clr-bg-base');
      document.documentElement.style.removeProperty('--clr-text-primary');
    }
  }

  function _applyAccent(name) {
    const a = ACCENTS[name];
    if (!a) return;
    const r = document.documentElement;
    r.style.setProperty('--clr-primary',      a.primary);
    r.style.setProperty('--clr-primary-dim',  a.dim);
    r.style.setProperty('--clr-primary-glow', a.glow);
  }

  function setTheme(theme) {
    localStorage.setItem(KEY_THEME, theme);
    _applyTheme(theme);
  }

  function setAccent(name) {
    localStorage.setItem(KEY_ACCENT, name);
    _applyAccent(name);
  }

  function getTheme()  { return localStorage.getItem(KEY_THEME)  || 'dark'; }
  function getAccent() { return localStorage.getItem(KEY_ACCENT) || 'blue'; }
  function getAccents() { return Object.keys(ACCENTS); }

  // Auto-init
  document.addEventListener('DOMContentLoaded', init);

  return { init, setTheme, setAccent, getTheme, getAccent, getAccents };
})();

window.ThemeManager = ThemeManager;
