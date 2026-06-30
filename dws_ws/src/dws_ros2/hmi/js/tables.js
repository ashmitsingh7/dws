/**
 * DWS Tables Module
 * Sortable, searchable, paginated data tables with CSV/JSON export.
 * Extends ui_manager.js DataTable — does NOT replace it.
 * Build 005.1 — Sprint 2
 */
'use strict';

const DWSTable = (() => {

  /**
   * Create a fully-managed table.
   *
   * @param {object} opts
   *   container  {HTMLElement|string}  — parent element or selector
   *   columns    {Array}               — [{ key, label, sortable, render, cls }]
   *   fetchData  {Function}            — async () => [] of row objects
   *   pageSize   {number}              — rows per page (default 25)
   *   searchKeys {Array}               — keys to search across
   *   filterDefs {Array}               — [{ key, label, options[] }]
   *   exportName {string}              — CSV filename without extension
   */
  function create(opts) {
    const {
      container: containerSel,
      columns = [],
      fetchData,
      pageSize = 25,
      searchKeys = [],
      filterDefs = [],
      exportName = 'dws-export',
      title = '',
      onRowClick,
    } = opts;

    const container = typeof containerSel === 'string'
      ? document.querySelector(containerSel)
      : containerSel;
    if (!container) { console.error('DWSTable: container not found'); return null; }

    // --- state ---
    let allData     = [];
    let filtered    = [];
    let sortKey     = null;
    let sortDir     = 'asc';
    let currentPage = 1;
    let searchTerm  = '';
    let activeFilters = {};

    // --- build chrome ---
    container.innerHTML = _buildChrome();
    const wrap      = container.querySelector('.table-wrap');
    const toolbar   = wrap.querySelector('.table-toolbar');
    const searchEl  = wrap.querySelector('.table-search input');
    const tbody     = wrap.querySelector('tbody');
    const paginEl   = wrap.querySelector('.table-pagination');
    const thead     = wrap.querySelector('thead tr');

    _buildColumns();

    if (searchEl) {
      searchEl.addEventListener('input', () => {
        searchTerm  = searchEl.value.trim().toLowerCase();
        currentPage = 1;
        _filter();
      });
    }

    // Export button
    const exportBtn = toolbar.querySelector('.btn-export');
    if (exportBtn) exportBtn.addEventListener('click', _exportCSV);

    // Refresh button
    const refreshBtn = toolbar.querySelector('.btn-refresh');
    if (refreshBtn) refreshBtn.addEventListener('click', load);

    // Filter dropdowns
    filterDefs.forEach(fd => {
      const sel = toolbar.querySelector(`[data-filter="${fd.key}"]`);
      if (sel) sel.addEventListener('change', () => {
        activeFilters[fd.key] = sel.value;
        currentPage = 1;
        _filter();
      });
    });

    async function load() {
      _setLoading(true);
      try {
        allData = await fetchData();
        _filter();
      } catch (err) {
        _showError('Failed to load data: ' + err.message);
      } finally {
        _setLoading(false);
      }
    }

    function _buildChrome() {
      const filterHtml = filterDefs.map(fd => `
        <select class="form-control" style="height:34px;font-size:var(--text-sm);min-width:140px;" data-filter="${fd.key}">
          <option value="">All ${fd.label}</option>
          ${fd.options.map(o => `<option value="${o.value}">${o.label}</option>`).join('')}
        </select>
      `).join('');

      return `
        <div class="table-wrap">
          <div class="table-toolbar">
            ${title ? `<span class="table-title">${title}</span>` : ''}
            <div class="table-search">
              <span class="search-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
              </span>
              <input type="search" placeholder="Search…" aria-label="Search table">
            </div>
            ${filterHtml}
            <div class="table-toolbar-spacer"></div>
            <button class="btn btn-ghost btn-sm btn-refresh" title="Refresh">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.18"/></svg>
            </button>
            <button class="btn btn-secondary btn-sm btn-export">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
              Export CSV
            </button>
          </div>
          <div style="overflow-x:auto;">
            <table class="data-table">
              <thead><tr></tr></thead>
              <tbody></tbody>
            </table>
          </div>
          <div class="table-empty" style="display:none;padding:48px;text-align:center;color:var(--clr-text-tertiary);font-size:var(--text-base);">
            No records found
          </div>
          <div class="table-pagination"></div>
        </div>
      `;
    }

    function _buildColumns() {
      thead.innerHTML = columns.map(col => `
        <th class="${col.sortable ? 'sortable' : ''}" data-key="${col.key}" style="${col.width ? 'width:' + col.width : ''}">
          ${col.label}
          ${col.sortable ? `<span class="sort-icon">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/></svg>
          </span>` : ''}
        </th>
      `).join('');

      thead.querySelectorAll('th.sortable').forEach(th => {
        th.addEventListener('click', () => {
          const key = th.getAttribute('data-key');
          if (sortKey === key) {
            sortDir = sortDir === 'asc' ? 'desc' : 'asc';
          } else {
            sortKey = key;
            sortDir = 'asc';
          }
          currentPage = 1;
          _render();
          _updateSortUI();
        });
      });
    }

    function _updateSortUI() {
      thead.querySelectorAll('th').forEach(th => {
        th.classList.remove('sort-asc', 'sort-desc');
        if (th.getAttribute('data-key') === sortKey) {
          th.classList.add('sort-' + sortDir);
        }
      });
    }

    function _filter() {
      filtered = allData.filter(row => {
        // search
        if (searchTerm && searchKeys.length) {
          const match = searchKeys.some(k => {
            const v = row[k];
            return v != null && String(v).toLowerCase().includes(searchTerm);
          });
          if (!match) return false;
        }
        // filters
        for (const [key, val] of Object.entries(activeFilters)) {
          if (!val) continue;
          if (String(row[key]) !== String(val)) return false;
        }
        return true;
      });
      _render();
    }

    function _render() {
      // sort
      if (sortKey) {
        filtered.sort((a, b) => {
          const av = a[sortKey], bv = b[sortKey];
          const cmp = av < bv ? -1 : av > bv ? 1 : 0;
          return sortDir === 'asc' ? cmp : -cmp;
        });
      }

      const total      = filtered.length;
      const totalPages = Math.max(1, Math.ceil(total / pageSize));
      currentPage      = Math.min(currentPage, totalPages);
      const start      = (currentPage - 1) * pageSize;
      const pageRows   = filtered.slice(start, start + pageSize);

      // empty state
      const emptyEl = wrap.querySelector('.table-empty');
      const tableEl = wrap.querySelector('table');
      if (total === 0) {
        if (emptyEl) emptyEl.style.display = '';
        if (tableEl) tableEl.style.display = 'none';
      } else {
        if (emptyEl) emptyEl.style.display = 'none';
        if (tableEl) tableEl.style.display = '';
      }

      tbody.innerHTML = pageRows.map(row => `
        <tr data-id="${row.id || ''}" style="cursor:${onRowClick ? 'pointer' : 'default'}">
          ${columns.map(col => {
            const raw = row[col.key];
            const cell = col.render ? col.render(raw, row) : (raw != null ? _escHtml(String(raw)) : '—');
            return `<td class="${col.cls || ''}">${cell}</td>`;
          }).join('')}
        </tr>
      `).join('');

      if (onRowClick) {
        tbody.querySelectorAll('tr').forEach((tr, i) => {
          tr.addEventListener('click', () => onRowClick(pageRows[i], tr));
        });
      }

      _renderPagination(total, totalPages, start);
    }

    function _renderPagination(total, totalPages, start) {
      if (!paginEl) return;
      const end = start + pageSize;
      paginEl.innerHTML = `
        <span class="pagination-info">
          ${total === 0 ? 'No records' : `${start + 1}–${Math.min(end, total)} of ${total}`}
        </span>
        <div class="pagination-controls" id="${container.id || 'tbl'}-pag-controls"></div>
      `;

      const controls = paginEl.querySelector('.pagination-controls');
      const mkBtn = (label, disabled, page) => {
        const b = document.createElement('button');
        b.className = 'pagination-btn' + (page === currentPage ? ' active' : '');
        b.textContent = label;
        b.disabled = disabled;
        if (!disabled) b.addEventListener('click', () => { currentPage = page; _render(); });
        return b;
      };

      controls.appendChild(mkBtn('‹', currentPage <= 1, currentPage - 1));

      let pages = [];
      if (totalPages <= 7) {
        pages = Array.from({ length: totalPages }, (_, i) => i + 1);
      } else {
        pages = [1];
        const lo = Math.max(2, currentPage - 1);
        const hi = Math.min(totalPages - 1, currentPage + 1);
        if (lo > 2) pages.push('…');
        for (let p = lo; p <= hi; p++) pages.push(p);
        if (hi < totalPages - 1) pages.push('…');
        pages.push(totalPages);
      }

      pages.forEach(p => {
        if (p === '…') {
          const s = document.createElement('span');
          s.textContent = '…';
          s.style.cssText = 'padding:0 6px;color:var(--clr-text-tertiary);align-self:center;';
          controls.appendChild(s);
        } else {
          controls.appendChild(mkBtn(String(p), false, p));
        }
      });

      controls.appendChild(mkBtn('›', currentPage >= totalPages, currentPage + 1));
    }

    function _setLoading(on) {
      const tableEl = wrap.querySelector('table');
      if (!tableEl) return;
      if (on) {
        tbody.innerHTML = `<tr><td colspan="${columns.length}" style="text-align:center;padding:48px;color:var(--clr-text-tertiary);">
          <div class="skeleton" style="height:14px;width:200px;margin:0 auto;border-radius:var(--radius-md);"></div>
        </td></tr>`;
      }
    }

    function _showError(msg) {
      tbody.innerHTML = `<tr><td colspan="${columns.length}" style="text-align:center;padding:32px;color:var(--clr-danger);">${_escHtml(msg)}</td></tr>`;
    }

    function _exportCSV() {
      const headers = columns.map(c => c.label).join(',');
      const rows = filtered.map(row =>
        columns.map(c => {
          const v = row[c.key] != null ? String(row[c.key]) : '';
          return `"${v.replace(/"/g, '""')}"`;
        }).join(',')
      ).join('\n');
      const blob = new Blob([headers + '\n' + rows], { type: 'text/csv' });
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement('a');
      a.href     = url;
      a.download = `${exportName}-${new Date().toISOString().slice(0,10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    }

    function _escHtml(s) {
      return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    // public API
    return { load, refresh: () => { currentPage = 1; _filter(); }, getData: () => filtered };
  }

  return { create };
})();

window.DWSTable = DWSTable;
