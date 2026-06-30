/**
 * DWS Chart Manager
 * Lightweight canvas chart primitives — no external chart library.
 * Reads colors live from CSS custom properties so charts always match
 * the active theme/accent (see theme_manager.js).
 * Build 005.1 — Sprint 3
 */
'use strict';

const ChartManager = (() => {

  function _cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function _dpiCanvas(canvas) {
    const ctx = canvas.getContext('2d');
    const W = canvas.clientWidth || canvas.width || 300;
    const H = canvas.clientHeight || canvas.height || 120;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, W, H };
  }

  /** Horizontal/line sparkline — array of numbers. */
  function sparkline(canvas, values, opts = {}) {
    if (typeof canvas === 'string') canvas = document.getElementById(canvas);
    if (!canvas || !values || !values.length) return;
    const { ctx, W, H } = _dpiCanvas(canvas);
    const color = opts.color || _cssVar('--clr-primary', '#1a8cff');
    const fill  = opts.fill !== false;

    ctx.clearRect(0, 0, W, H);
    const min = Math.min(...values), max = Math.max(...values);
    const range = max - min || 1;
    const stepX = W / Math.max(1, values.length - 1);

    const pts = values.map((v, i) => [i * stepX, H - ((v - min) / range) * (H - 6) - 3]);

    if (fill) {
      ctx.beginPath();
      ctx.moveTo(pts[0][0], H);
      pts.forEach(p => ctx.lineTo(p[0], p[1]));
      ctx.lineTo(pts[pts.length - 1][0], H);
      ctx.closePath();
      const grad = ctx.createLinearGradient(0, 0, 0, H);
      grad.addColorStop(0, color + '33');
      grad.addColorStop(1, color + '00');
      ctx.fillStyle = grad;
      ctx.fill();
    }

    ctx.beginPath();
    pts.forEach((p, i) => i === 0 ? ctx.moveTo(p[0], p[1]) : ctx.lineTo(p[0], p[1]));
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.75;
    ctx.lineJoin = 'round';
    ctx.stroke();

    const last = pts[pts.length - 1];
    ctx.beginPath();
    ctx.arc(last[0], last[1], 2.5, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
  }

  /** Circular gauge — value 0-100 (or custom min/max), with label. */
  function gauge(canvas, value, opts = {}) {
    if (typeof canvas === 'string') canvas = document.getElementById(canvas);
    if (!canvas) return;
    const { ctx, W, H } = _dpiCanvas(canvas);
    const min = opts.min ?? 0, max = opts.max ?? 100;
    const pct = Math.max(0, Math.min(1, (value - min) / (max - min)));
    const cx = W / 2, cy = H / 2 + (opts.label ? -4 : 0);
    const r  = Math.min(W, H) / 2 - 8;
    const start = Math.PI * 0.75, end = Math.PI * 2.25;
    const sweep = start + (end - start) * pct;

    const track = _cssVar('--clr-bg-raised', '#1c2535');
    let color = opts.color;
    if (!color) {
      if (pct > 0.85) color = _cssVar('--clr-danger', '#ef4444');
      else if (pct > 0.65) color = _cssVar('--clr-warning', '#f59e0b');
      else color = _cssVar('--clr-success', '#22c55e');
    }

    ctx.clearRect(0, 0, W, H);
    ctx.lineCap = 'round';

    ctx.beginPath();
    ctx.arc(cx, cy, r, start, end);
    ctx.strokeStyle = track;
    ctx.lineWidth = 10;
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(cx, cy, r, start, sweep);
    ctx.strokeStyle = color;
    ctx.lineWidth = 10;
    ctx.stroke();

    ctx.fillStyle = _cssVar('--clr-text-primary', '#e8edf5');
    ctx.font = `700 ${Math.round(r * 0.42)}px var(--font-sans, sans-serif)`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(opts.displayValue ?? Math.round(value), cx, cy);

    if (opts.label) {
      ctx.font = `500 ${Math.round(r * 0.16)}px var(--font-sans, sans-serif)`;
      ctx.fillStyle = _cssVar('--clr-text-tertiary', '#4d607d');
      ctx.fillText(opts.label, cx, cy + r * 0.42);
    }
  }

  /** Donut chart from [{label,value,color}] */
  function donut(canvas, segments, opts = {}) {
    if (typeof canvas === 'string') canvas = document.getElementById(canvas);
    if (!canvas || !segments || !segments.length) return;
    const { ctx, W, H } = _dpiCanvas(canvas);
    const cx = W / 2, cy = H / 2;
    const r  = Math.min(W, H) / 2 - 4;
    const inner = r * (opts.thickness ?? 0.6);
    const total = segments.reduce((a, s) => a + s.value, 0) || 1;

    ctx.clearRect(0, 0, W, H);
    let angle = -Math.PI / 2;
    segments.forEach(s => {
      const slice = (s.value / total) * Math.PI * 2;
      ctx.beginPath();
      ctx.arc(cx, cy, r, angle, angle + slice);
      ctx.arc(cx, cy, inner, angle + slice, angle, true);
      ctx.closePath();
      ctx.fillStyle = s.color || _cssVar('--clr-primary', '#1a8cff');
      ctx.fill();
      angle += slice;
    });

    if (opts.centerLabel) {
      ctx.fillStyle = _cssVar('--clr-text-primary', '#e8edf5');
      ctx.font = `700 ${Math.round(inner * 0.5)}px var(--font-sans, sans-serif)`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(opts.centerLabel, cx, cy);
    }
  }

  /** Grouped vertical bar chart from [{label, value}] */
  function barChart(canvas, buckets, opts = {}) {
    if (typeof canvas === 'string') canvas = document.getElementById(canvas);
    if (!canvas || !buckets) return;
    const { ctx, W, H } = _dpiCanvas(canvas);
    ctx.clearRect(0, 0, W, H);

    const color = opts.color || _cssVar('--clr-primary', '#1a8cff');
    const textColor = _cssVar('--clr-text-tertiary', '#4d607d');
    const gridColor = _cssVar('--clr-border-subtle', '#1a2235');

    const padL = 6, padR = 6, padT = 8, padB = 22;
    const innerW = W - padL - padR, innerH = H - padT - padB;
    const maxVal = Math.max(1, ...buckets.map(b => b.value));
    const barW = (innerW / buckets.length) * 0.55;
    const gap  = (innerW / buckets.length) * 0.45;

    ctx.strokeStyle = gridColor;
    ctx.lineWidth = 1;
    [0.25, 0.5, 0.75, 1].forEach(f => {
      const y = padT + innerH * (1 - f);
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
    });

    buckets.forEach((b, i) => {
      const x = padL + i * (barW + gap);
      const bh = Math.max(2, (b.value / maxVal) * innerH);
      const y = padT + innerH - bh;
      const grad = ctx.createLinearGradient(0, y, 0, y + bh);
      grad.addColorStop(0, color);
      grad.addColorStop(1, color + '55');
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.roundRect(x, y, barW, bh, [3, 3, 0, 0]);
      ctx.fill();

      if (opts.showLabels !== false && (buckets.length <= 12 || i % Math.ceil(buckets.length / 8) === 0)) {
        ctx.fillStyle = textColor;
        ctx.font = '10px var(--font-mono, monospace)';
        ctx.textAlign = 'center';
        ctx.fillText(b.label, x + barW / 2, H - 6);
      }
    });
  }

  /** Multi-series line chart from { labels:[], series:[{name,color,data:[]}] } */
  function lineChart(canvas, dataset, opts = {}) {
    if (typeof canvas === 'string') canvas = document.getElementById(canvas);
    if (!canvas || !dataset || !dataset.series) return;
    const { ctx, W, H } = _dpiCanvas(canvas);
    ctx.clearRect(0, 0, W, H);

    const gridColor = _cssVar('--clr-border-subtle', '#1a2235');
    const textColor = _cssVar('--clr-text-tertiary', '#4d607d');
    const padL = 28, padR = 8, padT = 10, padB = 22;
    const innerW = W - padL - padR, innerH = H - padT - padB;

    const allVals = dataset.series.flatMap(s => s.data);
    const max = opts.max ?? Math.max(1, ...allVals);
    const min = opts.min ?? Math.min(0, ...allVals);
    const range = (max - min) || 1;
    const n = dataset.labels.length;
    const stepX = innerW / Math.max(1, n - 1);

    ctx.strokeStyle = gridColor;
    ctx.lineWidth = 1;
    [0, 0.25, 0.5, 0.75, 1].forEach(f => {
      const y = padT + innerH * (1 - f);
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
      ctx.fillStyle = textColor;
      ctx.font = '9px var(--font-mono, monospace)';
      ctx.textAlign = 'right';
      ctx.fillText(Math.round(min + range * f), padL - 4, y + 3);
    });

    dataset.series.forEach(s => {
      const pts = s.data.map((v, i) => [padL + i * stepX, padT + innerH - ((v - min) / range) * innerH]);
      ctx.beginPath();
      pts.forEach((p, i) => i === 0 ? ctx.moveTo(p[0], p[1]) : ctx.lineTo(p[0], p[1]));
      ctx.strokeStyle = s.color || _cssVar('--clr-primary', '#1a8cff');
      ctx.lineWidth = 1.75;
      ctx.lineJoin = 'round';
      ctx.stroke();
    });

    if (opts.showLabels !== false) {
      ctx.fillStyle = textColor;
      ctx.font = '9px var(--font-mono, monospace)';
      ctx.textAlign = 'center';
      const stride = Math.ceil(n / 8) || 1;
      dataset.labels.forEach((l, i) => {
        if (i % stride === 0) ctx.fillText(l, padL + i * stepX, H - 6);
      });
    }
  }

  /** Re-render a canvas's chart on resize (throttled via ResizeObserver). */
  function autoResize(canvas, renderFn) {
    if (typeof canvas === 'string') canvas = document.getElementById(canvas);
    if (!canvas || !window.ResizeObserver) return;
    const ro = new ResizeObserver(() => renderFn());
    ro.observe(canvas);
    return ro;
  }

  return { sparkline, gauge, donut, barChart, lineChart, autoResize };
})();

window.ChartManager = ChartManager;
