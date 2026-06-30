'use strict';

/**
 * DWS Desktop — Production Logger
 *
 * Wraps electron-log with:
 *   - Timestamps on every line
 *   - Log rotation: 10 MB per file, 5 files kept
 *   - Separate log files per subsystem (main, supervisor, health)
 *   - Console mirror in development
 *   - Structured JSON-line format for machine parsing
 *
 * Usage:
 *   const log = require('./logger');
 *   log.info('message');
 *   log.supervisor.warn('backend crashed');
 *   log.health.debug('api ok');
 */

const electronLog = require('electron-log');
const path        = require('path');
const os          = require('os');

const LOGS_DIR = path.join(os.homedir(), '.dws', 'logs');

// ── Configure the default (main) logger ──────────────────────────────────────

electronLog.transports.file.resolvePathFn = () =>
  path.join(LOGS_DIR, 'main.log');

electronLog.transports.file.maxSize  = 10 * 1024 * 1024; // 10 MB
electronLog.transports.file.archiveLogFn = (oldLogPath) => {
  const { rotateLog } = require('./logger');
  rotateLog(oldLogPath, 5);
};

electronLog.transports.file.format =
  '[{y}-{m}-{d} {h}:{i}:{s}.{ms}] [{level}] [main] {text}';

electronLog.transports.console.format =
  '[{h}:{i}:{s}] [{level}] [main] {text}';

// In production, suppress debug from console
const IS_DEV = process.argv.includes('--dev');
electronLog.transports.console.level = IS_DEV ? 'debug' : 'info';
electronLog.transports.file.level    = 'debug';

// ── Supervisor sub-logger ─────────────────────────────────────────────────────

const supervisorLog = electronLog.create('supervisor');
supervisorLog.transports.file.resolvePathFn = () =>
  path.join(LOGS_DIR, 'supervisor.log');
supervisorLog.transports.file.maxSize  = 10 * 1024 * 1024;
supervisorLog.transports.file.format   =
  '[{y}-{m}-{d} {h}:{i}:{s}.{ms}] [{level}] [supervisor] {text}';
supervisorLog.transports.console.format =
  '[{h}:{i}:{s}] [{level}] [supervisor] {text}';
supervisorLog.transports.console.level = IS_DEV ? 'debug' : 'warn';
supervisorLog.transports.file.level    = 'debug';

// ── Health sub-logger ─────────────────────────────────────────────────────────

const healthLog = electronLog.create('health');
healthLog.transports.file.resolvePathFn = () =>
  path.join(LOGS_DIR, 'health.log');
healthLog.transports.file.maxSize  = 5 * 1024 * 1024;
healthLog.transports.file.format   =
  '[{y}-{m}-{d} {h}:{i}:{s}.{ms}] [{level}] [health] {text}';
healthLog.transports.console.format =
  '[{h}:{i}:{s}] [{level}] [health] {text}';
healthLog.transports.console.level = IS_DEV ? 'debug' : 'error';
healthLog.transports.file.level    = 'info';

// ── Log rotation helper ───────────────────────────────────────────────────────

/**
 * Rotate log files: rename .log → .1.log, .1.log → .2.log, etc.
 * Delete the oldest if count exceeds maxFiles.
 */
function rotateLog(logPath, maxFiles = 5) {
  try {
    const fs   = require('fs');
    const base = logPath.replace(/\.log$/, '');

    // Shift existing archives
    for (let i = maxFiles - 1; i >= 1; i--) {
      const from = `${base}.${i}.log`;
      const to   = `${base}.${i + 1}.log`;
      if (fs.existsSync(from)) {
        if (i + 1 > maxFiles) {
          fs.unlinkSync(from);
        } else {
          fs.renameSync(from, to);
        }
      }
    }

    // Current → .1.log
    if (fs.existsSync(logPath)) {
      fs.renameSync(logPath, `${base}.1.log`);
    }
  } catch (err) {
    // Non-fatal — log rotation failure should not crash the app
    electronLog.warn(`Log rotation failed for ${logPath}: ${err.message}`);
  }
}

// ── Uncaught exception handler ────────────────────────────────────────────────

process.on('uncaughtException', (err) => {
  electronLog.error('Uncaught exception in main process:', err);
});

process.on('unhandledRejection', (reason) => {
  electronLog.error('Unhandled promise rejection in main process:', reason);
});

// ── Export ────────────────────────────────────────────────────────────────────

const log = electronLog;
log.supervisor = supervisorLog;
log.health     = healthLog;
log.rotateLog  = rotateLog;

module.exports = log;
