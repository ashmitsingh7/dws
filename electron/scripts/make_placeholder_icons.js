#!/usr/bin/env node
/**
 * DWS Desktop — Generate placeholder tray icons and app icon.
 *
 * Produces minimal valid PNG files so `npm start` works before
 * a designer provides the real artwork.
 *
 * Run:  node scripts/make_placeholder_icons.js
 * Requires: npm install canvas  (only for this script)
 *
 * If canvas is unavailable, falls back to writing raw 1x1 PNGs.
 */

'use strict';

const fs   = require('fs');
const path = require('path');

const ICONS_DIR = path.join(__dirname, '..', 'assets', 'icons');
fs.mkdirSync(ICONS_DIR, { recursive: true });

const TRAY_ICONS = {
  'tray-ok.png':    '#00B4D8',  // accent blue
  'tray-warn.png':  '#F59E0B',  // amber
  'tray-error.png': '#EF4444',  // red
  'tray-idle.png':  '#4A5568',  // grey
};

// Try to use canvas for proper icons; fall back to minimal PNG bytes
let makeIcon;
try {
  const { createCanvas } = require('canvas');

  makeIcon = function(color, size) {
    const canvas = createCanvas(size, size);
    const ctx    = canvas.getContext('2d');

    // Background circle
    ctx.fillStyle = '#0F1923';
    ctx.fillRect(0, 0, size, size);

    // Colored dot
    const r = size * 0.35;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, r, 0, Math.PI * 2);
    ctx.fill();

    return canvas.toBuffer('image/png');
  };

  console.log('Using canvas for icon generation');
} catch (_) {
  // Minimal 1×1 PNG fallback — good enough for tray (OS scales it)
  makeIcon = function(color) {
    return minimalPng(color);
  };
  console.log('canvas not available — writing minimal 1×1 PNGs (install canvas for proper icons)');
}

// Write tray icons (22×22)
for (const [name, color] of Object.entries(TRAY_ICONS)) {
  const dest = path.join(ICONS_DIR, name);
  if (fs.existsSync(dest)) {
    console.log(`  skip (exists): ${name}`);
    continue;
  }
  fs.writeFileSync(dest, makeIcon(color, 22));
  console.log(`  created: ${name}  (${color})`);
}

// Write app icon (512×512)
const appIconDest = path.join(ICONS_DIR, 'icon.png');
if (!fs.existsSync(appIconDest)) {
  fs.writeFileSync(appIconDest, makeIcon('#00B4D8', 512));
  console.log('  created: icon.png (512×512)');
} else {
  console.log('  skip (exists): icon.png');
}

console.log('\nDone. Replace assets/icons/*.png with proper artwork before packaging.');

// ── Minimal valid PNG (pure Node, no dependencies) ────────────────────────────

function minimalPng(hex) {
  // 1×1 RGB PNG with the given colour.
  // Uses zlib via Node built-in.
  const zlib = require('zlib');
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);

  // IDAT raw data: filter byte (0) + R G B
  const raw    = Buffer.from([0, r, g, b]);
  const idat   = zlib.deflateSync(raw, { level: 9 });

  const crc32 = (buf) => {
    let c = 0xFFFFFFFF;
    const table = crc32.table || (crc32.table = (() => {
      const t = new Uint32Array(256);
      for (let i = 0; i < 256; i++) {
        let v = i;
        for (let k = 0; k < 8; k++) v = (v & 1) ? 0xEDB88320 ^ (v >>> 1) : v >>> 1;
        t[i] = v;
      }
      return t;
    })());
    for (let i = 0; i < buf.length; i++) c = table[(c ^ buf[i]) & 0xFF] ^ (c >>> 8);
    return (c ^ 0xFFFFFFFF) >>> 0;
  };

  const chunk = (type, data) => {
    const t   = Buffer.from(type, 'ascii');
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(Buffer.concat([t, data])));
    return Buffer.concat([len, t, data, crc]);
  };

  const sig  = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  const ihdr = chunk('IHDR', (() => {
    const b = Buffer.alloc(13);
    b.writeUInt32BE(1, 0);  // width
    b.writeUInt32BE(1, 4);  // height
    b[8]  = 8;              // bit depth
    b[9]  = 2;              // color type: RGB
    return b;
  })());
  const idatChunk = chunk('IDAT', idat);
  const iend      = chunk('IEND', Buffer.alloc(0));

  return Buffer.concat([sig, ihdr, idatChunk, iend]);
}
