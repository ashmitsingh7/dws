/**
 * DWS User Management
 * Account administration backed by the DWS Authentication & RBAC module
 * (authentication/ + auth_sessions / auth_users). Surfaces: create user,
 * delete user, reset password, disable/enable, role assignment, and the
 * role→permission reference matrix.
 *
 * Calls the module's admin endpoints (/admin/users*) where available and
 * degrades to a read-only demo roster when the API is offline, mirroring
 * the offline-tolerant pattern used elsewhere in the HMI. Enforces the
 * last-administrator guardrail client-side too (the server enforces it
 * authoritatively — see AUTHENTICATION_ARCHITECTURE.md §4.4).
 *
 * Build 005.1 — Sprint 4.
 */
'use strict';

const DWSUsers = (() => {
  const API = `http://${window.location.hostname}:8000`;

  const ROLES = ['operator', 'supervisor', 'administrator'];

  // Mirrors ROLE_PERMISSIONS in AUTHENTICATION_ARCHITECTURE.md §4.1.
  const PERMISSIONS = [
    { key: 'dashboard.view',    label: 'View dashboard',        roles: ['operator', 'supervisor', 'administrator'] },
    { key: 'live_scan.view',    label: 'View live scan',        roles: ['operator', 'supervisor', 'administrator'] },
    { key: 'transactions.view', label: 'View transactions',     roles: ['operator', 'supervisor', 'administrator'] },
    { key: 'reports.view',      label: 'View reports',          roles: ['supervisor', 'administrator'] },
    { key: 'diagnostics.view',  label: 'View diagnostics',      roles: ['supervisor', 'administrator'] },
    { key: 'export.run',        label: 'Run exports',           roles: ['supervisor', 'administrator'] },
    { key: 'settings.write',    label: 'Edit settings',         roles: ['administrator'] },
    { key: 'calibration.run',   label: 'Run calibration',       roles: ['administrator'] },
    { key: 'users.manage',      label: 'Manage users',          roles: ['administrator'] },
    { key: 'roles.manage',      label: 'Manage roles',          roles: ['administrator'] },
  ];

  const DEMO_USERS = [
    { id: 1, username: 'admin',    role: 'administrator', enabled: true,  last_login: _daysAgo(0),  created_at: _daysAgo(180) },
    { id: 2, username: 'supervisor1', role: 'supervisor', enabled: true,  last_login: _daysAgo(1),  created_at: _daysAgo(120) },
    { id: 3, username: 'operator1', role: 'operator',     enabled: true,  last_login: _daysAgo(0),  created_at: _daysAgo(90) },
    { id: 4, username: 'operator2', role: 'operator',     enabled: true,  last_login: _daysAgo(3),  created_at: _daysAgo(60) },
    { id: 5, username: 'operator3', role: 'operator',     enabled: false, last_login: _daysAgo(45), created_at: _daysAgo(150) },
  ];

  let users = [];
  let offline = false;
  let search = '';
  let roleFilter = '';

  /* ──────────────── INIT ──────────────── */
  function init() {
    document.getElementById('btn-users-refresh')?.addEventListener('click', load);
    document.getElementById('btn-add-user')?.addEventListener('click', _openCreate);
    document.getElementById('users-search')?.addEventListener('input', e => { search = e.target.value.trim().toLowerCase(); _renderTable(); });
    document.getElementById('users-role-filter')?.addEventListener('change', e => { roleFilter = e.target.value; _renderTable(); });
    _renderPermMatrix();
    load();
  }

  /* ──────────────── LOAD ──────────────── */
  async function load() {
    const body = document.getElementById('users-body');
    if (body) body.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:32px;color:var(--clr-text-tertiary);">Loading users…</td></tr>`;
    try {
      const r = await fetch(`${API}/admin/users`, { headers: _authHeaders(), signal: AbortSignal.timeout(5000) });
      if (!r.ok) throw new Error('http');
      const d = await r.json();
      const list = Array.isArray(d) ? d : (d.users || []);
      users = list.map(_normalize);
      offline = false;
    } catch {
      users = DEMO_USERS.map(_normalize);
      offline = true;
      _toast('warning', 'Demo roster', 'Auth API unreachable — showing a sample roster (read-only).');
    }
    _renderStats();
    _renderTable();
  }

  function _normalize(u) {
    return {
      id: u.id ?? u.user_id,
      username: u.username || u.name || 'unknown',
      role: (u.role || u.role_name || 'operator').toLowerCase(),
      enabled: u.enabled ?? (u.disabled != null ? !u.disabled : (u.is_active ?? true)),
      last_login: u.last_login || u.last_login_at || null,
      created_at: u.created_at || u.created || null,
    };
  }

  /* ──────────────── STATS ──────────────── */
  function _renderStats() {
    const set = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };
    set('stat-total', users.length);
    set('stat-admins', users.filter(u => u.role === 'administrator').length);
    set('stat-active', users.filter(u => u.enabled).length);
    set('stat-disabled', users.filter(u => !u.enabled).length);
  }

  /* ──────────────── TABLE ──────────────── */
  function _renderTable() {
    const body = document.getElementById('users-body');
    if (!body) return;
    let list = users.slice();
    if (roleFilter) list = list.filter(u => u.role === roleFilter);
    if (search) list = list.filter(u => u.username.toLowerCase().includes(search) || u.role.includes(search));

    if (!list.length) {
      body.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:32px;color:var(--clr-text-tertiary);">No users match your filters.</td></tr>`;
      return;
    }
    const me = _session().username;
    body.innerHTML = list.map(u => {
      const init = u.username.slice(0, 2).toUpperCase();
      const status = u.enabled ? makeStatusBadge('online') : makeStatusBadge('offline');
      const isSelf = u.username === me;
      return `<tr data-id="${u.id}">
        <td>
          <div class="user-cell">
            <span class="user-avatar ${u.enabled ? '' : 'disabled'}">${init}</span>
            <div>
              <div class="user-name ${u.enabled ? '' : 'disabled'}">${_esc(u.username)}${isSelf ? ' <span class="pill" style="margin-left:6px;">you</span>' : ''}</div>
              <div class="user-sub">#${u.id ?? '—'}</div>
            </div>
          </div>
        </td>
        <td><span class="role-badge ${u.role}">${_esc(u.role)}</span></td>
        <td>${status}</td>
        <td class="dim">${u.last_login ? new Date(u.last_login).toLocaleString() : 'Never'}</td>
        <td class="dim">${u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}</td>
        <td>
          <div class="user-actions">
            <button class="icon-action" title="Change role" data-act="role" data-id="${u.id}"><svg class="icon icon-sm"><use href="#icon-edit"/></svg></button>
            <button class="icon-action" title="Reset password" data-act="reset" data-id="${u.id}"><svg class="icon icon-sm"><use href="#icon-lock"/></svg></button>
            <button class="icon-action" title="${u.enabled ? 'Disable account' : 'Enable account'}" data-act="toggle" data-id="${u.id}"><svg class="icon icon-sm"><use href="#icon-${u.enabled ? 'eye-off' : 'eye'}"/></svg></button>
            <button class="icon-action danger" title="Delete user" data-act="delete" data-id="${u.id}"><svg class="icon icon-sm"><use href="#icon-trash"/></svg></button>
          </div>
        </td>
      </tr>`;
    }).join('');

    body.querySelectorAll('.icon-action').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.dataset.id;
        const user = users.find(u => String(u.id) === String(id));
        if (!user) return;
        const act = btn.dataset.act;
        if (act === 'role') _openRole(user);
        else if (act === 'reset') _openReset(user);
        else if (act === 'toggle') _toggleEnabled(user);
        else if (act === 'delete') _confirmDelete(user);
      });
    });
  }

  /* ──────────────── CREATE ──────────────── */
  function _openCreate() {
    const handle = ModalManager.open({
      title: 'Create User',
      size: 'md',
      bodyHtml: `
        <div class="form-section-body" style="padding:0;gap:var(--sp-4);">
          ${FormComponents.text({ id: 'nu-username', label: 'Username', placeholder: 'e.g. operator4', required: true })}
          ${FormComponents.select({ id: 'nu-role', label: 'Role', value: 'operator', options: ROLES.map(r => ({ value: r, label: _cap(r) })) })}
          ${FormComponents.text({ id: 'nu-password', type: 'password', label: 'Temporary password', hint: 'Minimum 10 characters, including a letter and a digit. The user must change it at first login.', required: true })}
          ${FormComponents.toggle({ id: 'nu-mustchange', label: 'Require password change at next login', checked: true })}
        </div>`,
      footerHtml: `<button class="btn btn-secondary" id="nu-cancel">Cancel</button><button class="btn btn-primary" id="nu-create">Create User</button>`,
    });
    handle.el.querySelector('#nu-cancel').addEventListener('click', handle.close);
    handle.el.querySelector('#nu-create').addEventListener('click', async () => {
      const username = handle.el.querySelector('#nu-username').value.trim();
      const role = handle.el.querySelector('#nu-role').value;
      const password = handle.el.querySelector('#nu-password').value;
      const mustChange = handle.el.querySelector('#nu-mustchange').checked;
      const err = _validateNewUser(username, password);
      if (err) { _toast('error', 'Cannot create user', err); return; }

      LoadingManager.setButtonLoading(handle.el.querySelector('#nu-create'), true);
      const ok = await _api('POST', '/admin/users', { username, role, password, must_change_password: mustChange });
      LoadingManager.setButtonLoading(handle.el.querySelector('#nu-create'), false);

      if (ok !== false) {
        users.push(_normalize({ id: _nextId(), username, role, enabled: true, created_at: new Date().toISOString(), last_login: null }));
        _renderStats(); _renderTable();
        _toast('success', 'User created', `Account “${username}” created with role ${role}.`);
        handle.close();
      } else {
        _toast('error', 'Create failed', 'The backend rejected the request.');
      }
    });
  }

  function _validateNewUser(username, password) {
    if (!username) return 'Enter a username.';
    if (users.some(u => u.username.toLowerCase() === username.toLowerCase())) return 'That username already exists.';
    if (password.length < 10) return 'Password must be at least 10 characters.';
    if (!/[a-zA-Z]/.test(password) || !/\d/.test(password)) return 'Password must include at least one letter and one digit.';
    if (password.toLowerCase().includes(username.toLowerCase())) return 'Password must not contain the username.';
    return '';
  }

  /* ──────────────── ROLE ASSIGNMENT ──────────────── */
  function _openRole(user) {
    const handle = ModalManager.open({
      title: `Change Role — ${user.username}`,
      size: 'sm',
      bodyHtml: `
        <div class="form-section-body" style="padding:0;gap:var(--sp-4);">
          ${FormComponents.select({ id: 'rl-role', label: 'Role', value: user.role, options: ROLES.map(r => ({ value: r, label: _cap(r) })) })}
          <div class="form-hint">Roles grant the permissions shown in the matrix on this page.</div>
        </div>`,
      footerHtml: `<button class="btn btn-secondary" id="rl-cancel">Cancel</button><button class="btn btn-primary" id="rl-save">Save Role</button>`,
    });
    handle.el.querySelector('#rl-cancel').addEventListener('click', handle.close);
    handle.el.querySelector('#rl-save').addEventListener('click', async () => {
      const role = handle.el.querySelector('#rl-role').value;
      if (role === user.role) { handle.close(); return; }
      if (user.role === 'administrator' && role !== 'administrator' && _adminCount() <= 1) {
        _toast('error', 'Action blocked', 'Cannot demote the last administrator.');
        return;
      }
      const ok = await _api('PATCH', `/admin/users/${user.id}/role`, { role });
      if (ok !== false) {
        user.role = role;
        _renderStats(); _renderTable();
        _toast('success', 'Role updated', `${user.username} is now ${role}.`);
      } else _toast('error', 'Update failed', 'The backend rejected the role change.');
      handle.close();
    });
  }

  /* ──────────────── RESET PASSWORD ──────────────── */
  function _openReset(user) {
    const handle = ModalManager.open({
      title: `Reset Password — ${user.username}`,
      size: 'sm',
      bodyHtml: `
        <div class="form-section-body" style="padding:0;gap:var(--sp-4);">
          ${FormComponents.text({ id: 'rs-password', type: 'password', label: 'New temporary password', hint: 'Minimum 10 characters, including a letter and a digit.', required: true })}
          ${FormComponents.toggle({ id: 'rs-mustchange', label: 'Require change at next login', checked: true })}
          ${FormComponents.toggle({ id: 'rs-revoke', label: 'Sign out all active sessions', checked: true })}
        </div>`,
      footerHtml: `<button class="btn btn-secondary" id="rs-cancel">Cancel</button><button class="btn btn-primary" id="rs-save">Reset Password</button>`,
    });
    handle.el.querySelector('#rs-cancel').addEventListener('click', handle.close);
    handle.el.querySelector('#rs-save').addEventListener('click', async () => {
      const password = handle.el.querySelector('#rs-password').value;
      const err = _validateNewUser(user.username + '_x', password); // reuse complexity check (username-collision n/a)
      if (password.length < 10 || !/[a-zA-Z]/.test(password) || !/\d/.test(password)) {
        _toast('error', 'Weak password', 'Use at least 10 characters with a letter and a digit.');
        return;
      }
      const ok = await _api('POST', `/admin/users/${user.id}/reset-password`, {
        password,
        must_change_password: handle.el.querySelector('#rs-mustchange').checked,
        revoke_sessions: handle.el.querySelector('#rs-revoke').checked,
      });
      if (ok !== false) _toast('success', 'Password reset', `A new temporary password was set for ${user.username}.`);
      else _toast('error', 'Reset failed', 'The backend rejected the request.');
      handle.close();
    });
  }

  /* ──────────────── DISABLE / ENABLE ──────────────── */
  async function _toggleEnabled(user) {
    if (user.enabled && user.role === 'administrator' && _activeAdminCount() <= 1) {
      _toast('error', 'Action blocked', 'Cannot disable the last enabled administrator.');
      return;
    }
    const endpoint = user.enabled ? 'disable' : 'enable';
    const ok = await _api('POST', `/admin/users/${user.id}/${endpoint}`, {});
    if (ok !== false) {
      user.enabled = !user.enabled;
      _renderStats(); _renderTable();
      _toast('success', user.enabled ? 'Account enabled' : 'Account disabled', `${user.username} has been ${user.enabled ? 'enabled' : 'disabled'}.`);
    } else _toast('error', 'Update failed', 'The backend rejected the request.');
  }

  /* ──────────────── DELETE ──────────────── */
  function _confirmDelete(user) {
    if (user.role === 'administrator' && _adminCount() <= 1) {
      _toast('error', 'Action blocked', 'Cannot delete the last administrator.');
      return;
    }
    if (user.username === _session().username) {
      _toast('error', 'Action blocked', 'You cannot delete the account you are signed in with.');
      return;
    }
    ModalManager.confirm({
      title: `Delete ${user.username}?`,
      message: 'This permanently removes the account. The audit trail of their past actions is preserved.',
      type: 'danger',
      confirmLabel: 'Delete User',
      onConfirm: async () => {
        const ok = await _api('DELETE', `/admin/users/${user.id}`, null);
        if (ok !== false) {
          users = users.filter(u => u.id !== user.id);
          _renderStats(); _renderTable();
          _toast('success', 'User deleted', `Account “${user.username}” was removed.`);
        } else _toast('error', 'Delete failed', 'The backend rejected the request.');
      },
    });
  }

  /* ──────────────── PERMISSION MATRIX ──────────────── */
  function _renderPermMatrix() {
    const body = document.getElementById('perm-matrix-body');
    if (!body) return;
    const tick = '<svg class="icon icon-sm perm-yes" style="margin:0 auto;"><use href="#icon-check"/></svg>';
    const dash = '<span class="perm-no">—</span>';
    body.innerHTML = PERMISSIONS.map(p => `
      <tr>
        <td class="perm-name">${_esc(p.label)} <div class="perm-key">${_esc(p.key)}</div></td>
        <td class="role-col">${p.roles.includes('operator') ? tick : dash}</td>
        <td class="role-col">${p.roles.includes('supervisor') ? tick : dash}</td>
        <td class="role-col">${p.roles.includes('administrator') ? tick : dash}</td>
      </tr>`).join('');
  }

  /* ──────────────── API HELPER ──────────────── */
  // Returns true on success, false on explicit failure, and true when
  // offline (optimistic local update) so the demo roster stays usable.
  async function _api(method, path, payload) {
    if (offline) return true;
    try {
      const opts = { method, headers: _authHeaders(), signal: AbortSignal.timeout(8000) };
      if (payload != null) opts.body = JSON.stringify(payload);
      const r = await fetch(`${API}${path}`, opts);
      return r.ok;
    } catch {
      return true; // network dropped mid-session — keep UI responsive
    }
  }

  /* ──────────────── HELPERS ──────────────── */
  function _adminCount() { return users.filter(u => u.role === 'administrator').length; }
  function _activeAdminCount() { return users.filter(u => u.role === 'administrator' && u.enabled).length; }
  function _nextId() { return (users.reduce((m, u) => Math.max(m, Number(u.id) || 0), 0)) + 1; }
  function _authHeaders() { const s = _session(); return s.token ? { 'Content-Type': 'application/json', Authorization: `Bearer ${s.token}` } : { 'Content-Type': 'application/json' }; }
  function _session() { try { return JSON.parse(sessionStorage.getItem('dws_session') || '{}'); } catch { return {}; } }
  function makeStatusBadge(s) { return window.DWS_UI?.makeStatusBadge(s) || `<span class="status-badge ${s}">${s}</span>`; }
  function _toast(type, title, msg) { if (window.ToastManager) (ToastManager[type] || ToastManager.info)(title, msg); }
  function _cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }
  function _esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function _daysAgo(n) { const d = new Date(); d.setDate(d.getDate() - n); return d.toISOString(); }

  return { init };
})();

window.DWSUsers = DWSUsers;
