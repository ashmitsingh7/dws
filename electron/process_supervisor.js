'use strict';

/**
 * DWS Desktop — Process Supervisor  (Sprint 2 — production)
 *
 * Spawns and manages the three DWS backend processes:
 *   backend  ros2 run dws_ros2 run_dws  (WS :8765, MJPEG :8090)
 *   api      uvicorn api_server:app      (HTTP :8000)
 *   hmi      python3 -m http.server      (HTTP :8080)
 *
 * Features:
 *   - Systemd co-existence detection (monitor-only mode)
 *   - Crash-restart with exponential back-off (2s → 30s, give up at 5 crashes/60s)
 *   - Per-process log files piped to ~/.dws/logs/<name>.log
 *   - PID files written to ~/.dws/run/<name>.pid
 *   - SIGTERM → SIGKILL grace period (5 s)
 *   - ROS environment sourced from /opt/ros/jazzy or /opt/ros/humble
 *   - Falls back to direct python3 invocation if colcon build absent
 *
 * PACKAGING NOTES:
 *   In a packaged build (app.isPackaged === true):
 *     - this._root  →  <resourcesPath>/dws_ws           (read-only)
 *     - venv python →  this._root/.venv/bin/python3
 *     - dws_ros2 must resolve from .venv/lib/.../site-packages/, NOT src/
 *     - PYTHONPATH does NOT include src/dws_ros2 (that would re-introduce
 *       the source-tree dependency that normal install is meant to eliminate)
 *     - process.env.PYTHONPATH is NOT inherited (would leak developer paths)
 */

const { spawn }    = require('child_process');
const { execSync } = require('child_process');
const path         = require('path');
const fs           = require('fs');
const os           = require('os');
const net          = require('net');
const EventEmitter = require('events');

const RESTART_BASE_MS         = 2_000;
const RESTART_MAX_MS          = 30_000;
const MAX_CRASHES             = 5;
const CRASH_WINDOW_MS         = 60_000;
const STOP_GRACE_MS           = 5_000;
const BACKEND_READY_TIMEOUT_MS = 30_000;

const PID_DIR  = path.join(os.homedir(), '.dws', 'run');
const LOGS_DIR = path.join(os.homedir(), '.dws', 'logs');

const PROCESS_NAMES = ['backend', 'api', 'hmi'];

const PROCESS_PORTS = { backend: 8765, api: 8000, hmi: 8080 };

class ProcessSupervisor extends EventEmitter {
  constructor({ backendRoot, venvPython, noHardware = false, isDev = false, log, onStatusChange }) {
    super();

    this._root    = backendRoot;
    this._python  = venvPython;
    this._noHW    = noHardware;
    this._isDev   = isDev;
    this._log     = log || console;

    this._procs           = { backend: null, api: null, hmi: null };
    this._crashes         = { backend: [], api: [], hmi: [] };
    this._retimers        = { backend: null, api: null, hmi: null };
    this._logStreams       = { backend: null, api: null, hmi: null };
    this._stopping        = false;
    this._monitorOnly     = false;
    this._externallyManaged = { backend: false, api: false, hmi: false };

    if (onStatusChange) this.on('status', onStatusChange);

    this._ensureDirs();
    this._rosEnv = this._buildRosEnv();
  }

  // ── Public ─────────────────────────────────────────────────────────────────

  async startAll() {
    this._stopping = false;

    if (await this._isSystemdManaged()) {
      this._monitorOnly = true;
      this._log.info('Systemd-managed backend detected — entering monitor-only mode');
      this._emitStatus();
      return;
    }

    this._monitorOnly = false;
    this._log.info('Starting all backend processes');

    const identityOk = this._verifyInterpreterIdentity();
    if (!identityOk) {
      this._log.error(
        'Interpreter identity check FAILED — refusing to start backend/api. ' +
        'dws_ros2 resolves to a path outside this installation. ' +
        'Re-install with a clean venv built by build_venv.sh.'
      );
      this.emit('fatal', {
        reason: 'interpreter-identity-mismatch',
        message: 'The Python interpreter does not resolve dws_ros2 from this ' +
          "installation's bundled site-packages. This usually means a stale " +
          '.venv from a different checkout, or a system-level editable install, ' +
          'is shadowing the bundled package.',
      });
      await this._spawn('hmi');
      this._emitStatus();
      return;
    }

    await this._spawn('backend');

    const backendUp = await waitForPort(PROCESS_PORTS.backend, BACKEND_READY_TIMEOUT_MS);
    if (!backendUp) {
      this._log.warn(
        `backend did not open port ${PROCESS_PORTS.backend} within ` +
        `${BACKEND_READY_TIMEOUT_MS / 1000}s — starting api anyway.`
      );
    }

    await this._spawn('api');
    await this._spawn('hmi');
    this._emitStatus();
  }

  async stopAll() {
    this._stopping = true;
    if (this._monitorOnly) {
      this._log.info('Monitor-only mode — not stopping systemd processes');
      return;
    }

    this._log.info('Stopping all backend processes');

    for (const name of PROCESS_NAMES) {
      if (this._retimers[name]) {
        clearTimeout(this._retimers[name]);
        this._retimers[name] = null;
      }
    }

    await this._kill('hmi');
    await this._kill('api');
    await this._kill('backend');

    for (const name of PROCESS_NAMES) {
      try { this._logStreams[name]?.end(); } catch (_) {}
      this._logStreams[name] = null;
    }

    this._emitStatus();
    this._log.info('All backend processes stopped');
  }

  async restartAll() {
    this._log.info('Restarting all backend processes');
    await this.stopAll();
    await sleep(1000);
    await this.startAll();
  }

  getStatus() {
    return {
      backend:     this._alive('backend'),
      api:         this._alive('api'),
      hmi:         this._alive('hmi'),
      monitorOnly: this._monitorOnly,
      pids: {
        backend: this._procs.backend?.pid ?? null,
        api:     this._procs.api?.pid     ?? null,
        hmi:     this._procs.hmi?.pid     ?? null,
      },
    };
  }

  // ── Spawn ──────────────────────────────────────────────────────────────────

  async _spawn(name) {
    if (this._alive(name)) {
      this._log.warn(`${name} already running (pid ${this._procs[name].pid})`);
      return;
    }

    const port = PROCESS_PORTS[name];
    if (port && await isPortOpen(port)) {
      this._log.info(
        `Port ${port} already in use — assuming ${name} is externally managed.`
      );
      this._externallyManaged[name] = true;
      this._writePid(name, null);
      this._emitStatus();
      return;
    }
    this._externallyManaged[name] = false;

    const { cmd, args, cwd, env } = this._buildCmd(name);
    this._log.info(`Spawning ${name}: ${cmd} ${args.join(' ')}`);
    this._log.info(`  cwd: ${cwd}`);

    const logPath = path.join(LOGS_DIR, `${name}.log`);
    if (!this._logStreams[name] || this._logStreams[name].destroyed) {
      this._logStreams[name] = fs.createWriteStream(logPath, { flags: 'a' });
    }
    const stream = this._logStreams[name];

    stream.write(`\n--- DWS ${name} started ${new Date().toISOString()} ---\n`);

    const proc = spawn(cmd, args, {
      cwd,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: false,
    });

    proc.stdout.pipe(stream, { end: false });
    proc.stderr.pipe(stream, { end: false });

    proc.on('error', (err) => {
      this._log.error(`${name} spawn error: ${err.message}`);
      this._writePid(name, null);
      this._procs[name] = null;
      this._emitStatus();
      if (!this._stopping) this._scheduleRestart(name, 1);
    });

    proc.on('exit', (code, signal) => {
      this._log.warn(`${name} exited (code=${code} signal=${signal})`);
      this._writePid(name, null);
      this._procs[name] = null;
      this._emitStatus();
      if (!this._stopping) this._maybeCrashRestart(name, code, signal);
    });

    this._procs[name] = proc;
    this._writePid(name, proc.pid);
    this._log.info(`${name} started (pid ${proc.pid})`);
  }

  // ── Kill ───────────────────────────────────────────────────────────────────

  async _kill(name) {
    const proc = this._procs[name];
    if (!proc) return;

    return new Promise((resolve) => {
      let done = false;
      const finish = () => { if (!done) { done = true; resolve(); } };

      proc.once('exit', finish);

      try { proc.kill('SIGTERM'); } catch (_) {}

      setTimeout(() => {
        if (done) return;
        try { proc.kill('SIGKILL'); } catch (_) {}
        setTimeout(finish, 1000);
      }, STOP_GRACE_MS);
    });
  }

  // ── Build command ──────────────────────────────────────────────────────────

  _buildCmd(name) {
    if (!fs.existsSync(this._python)) {
      this._log.warn(
        `Configured Python interpreter not found at '${this._python}' — falling back to 'python3' on PATH.`
      );
      this._python = 'python3';
    }

    const rosSetup    = this._findRosSetup();
    const colconSetup = path.join(this._root, 'install', 'setup.bash');
    const hasColcon   = fs.existsSync(colconSetup);

    // ── PYTHONPATH construction ────────────────────────────────────────────
    //
    // PACKAGED mode (app.isPackaged / !this._isDev):
    //   In a clean package build, dws_ros2 is installed into
    //   .venv/lib/pythonX.Y/site-packages/ by a non-editable pip install.
    //   The venv python already knows about it — no PYTHONPATH needed for
    //   the package itself.
    //
    //   We still include the ROS2-sourced PYTHONPATH (this._rosEnv.PYTHONPATH)
    //   so ROS2 Python bindings (rclpy, etc.) remain importable when the
    //   backend runs under `ros2 run`. That path comes from
    //   /opt/ros/{jazzy,humble}/setup.bash on the target machine and is
    //   therefore always correct for the machine that is actually running.
    //
    //   We do NOT include:
    //     - src/dws_ros2 (source tree — defeats the point of packaging)
    //     - process.env.PYTHONPATH (developer shell leakage)
    //
    // DEV mode (this._isDev):
    //   Include src/dws_ros2 so developers can edit code without rebuilding,
    //   and inherit process.env.PYTHONPATH for maximum compatibility with
    //   the documented dev-setup workflow.
    //
    const pythonPathParts = [
      this._rosEnv.PYTHONPATH || '',
    ];
    if (this._isDev) {
      // Dev: add the source directory so edits are picked up immediately.
      pythonPathParts.unshift(path.join(this._root, 'src', 'dws_ros2'));
      // Dev: inherit shell PYTHONPATH (e.g. from an activated dev venv).
      pythonPathParts.push(process.env.PYTHONPATH || '');
    }
    // Packaged: dws_ros2 is in site-packages — no src path needed or wanted.

    const env = {
      ...process.env,
      ...this._rosEnv,
      PYTHONDONTWRITEBYTECODE: '1',
      PYTHONUNBUFFERED:        '1',
      DWS_RUNTIME_ROOT: path.join(os.homedir(), '.local', 'share', 'DWS'),
      PYTHONPATH: pythonPathParts.filter(Boolean).join(':'),
    };

    // In packaged mode, explicitly unset PYTHONPATH if it is empty to avoid
    // the venv picking up a stale empty-string entry (harmless but untidy).
    if (!env.PYTHONPATH) delete env.PYTHONPATH;

    switch (name) {
      case 'backend': {
        const extraFlags = [
          ...(this._noHW  ? ['--no-hardware'] : []),
          ...(this._isDev ? ['--dev']          : []),
        ];

        if (rosSetup && hasColcon) {
          return {
            cmd:  'bash',
            args: [
              '-c',
              [
                `source ${rosSetup}`,
                `source ${colconSetup}`,
                `ros2 run dws_ros2 run_dws ${extraFlags.join(' ')}`,
              ].join(' && '),
            ],
            cwd: this._root,
            env,
          };
        }

        // Fallback: direct python invocation (no ROS or no colcon build).
        // cwd is the src directory so relative imports inside run_dws.py work.
        return {
          cmd:  this._python,
          args: ['-m', 'dws_ros2.run_dws', ...extraFlags],
          cwd:  path.join(this._root, 'src', 'dws_ros2'),
          env,
        };
      }

      case 'api': {
        // api_server.py lives in src/dws_ros2/ and is run as a module-level
        // script rather than a proper Python package entry point; cwd must
        // be the directory that contains api_server.py.
        const apiSrc = path.join(this._root, 'src', 'dws_ros2');
        return {
          cmd:  this._python,
          args: [
            '-m', 'uvicorn', 'api_server:app',
            '--host', '0.0.0.0',
            '--port', '8000',
            '--log-level', this._isDev ? 'debug' : 'info',
          ],
          cwd: apiSrc,
          env,
        };
      }

      case 'hmi': {
        // Serve static files from the hmi/ directory that was either:
        //   packaged: resources/dws_ws/src/dws_ros2/hmi/
        //   dev:      <repo>/dws_ws/src/dws_ros2/hmi/
        const hmiDir = path.join(this._root, 'src', 'dws_ros2', 'hmi');
        return {
          cmd:  this._python,
          args: ['-m', 'http.server', '8080', '--bind', '0.0.0.0'],
          cwd:  hmiDir,
          env,
        };
      }

      default:
        throw new Error(`Unknown process: ${name}`);
    }
  }

  // ── Interpreter identity check ─────────────────────────────────────────────

  /**
   * Verify that the configured Python interpreter resolves dws_ros2 from
   * within this installation's bundled resources, not from a developer
   * source tree or a different machine's checkout.
   *
   * FATAL in both dev and packaged mode when the resolved path points
   * OUTSIDE this._root entirely — that means a foreign install is winning.
   *
   * Non-fatal when dws_ros2 is simply not importable in packaged mode
   * (the venv may have been copied to a different prefix and needs
   * PYTHONPATH from _buildCmd to find it at runtime — the check here
   * uses the interpreter directly without setting that PYTHONPATH).
   */
  _verifyInterpreterIdentity() {
    try {
      const out = execSync(
        `"${this._python}" -c "import importlib.util as u; s = u.find_spec('dws_ros2'); print(s.origin if s else '')"`,
        { encoding: 'utf8', timeout: 10_000, stdio: ['ignore', 'pipe', 'pipe'] }
      ).trim();

      if (!out) {
        // dws_ros2 not importable without PYTHONPATH assistance.
        // In packaged mode this is expected if the venv was relocated — the
        // runtime PYTHONPATH in _buildCmd supplies the missing path.
        // In dev mode it is a configuration error.
        if (this._isDev) {
          this._log.error(`Interpreter identity check: dws_ros2 not importable via ${this._python}`);
          return false;
        }
        this._log.info(
          'Interpreter identity check: dws_ros2 not importable without PYTHONPATH ' +
          '(normal for a relocated packaged venv — runtime env will supply it).'
        );
        return true;
      }

      const resolvedOrigin = path.resolve(out);
      const expectedRoot   = path.resolve(this._root);

      if (!resolvedOrigin.startsWith(expectedRoot + path.sep) && resolvedOrigin !== expectedRoot) {
        // The interpreter resolves dws_ros2 to somewhere entirely outside
        // this installation root. This is always a bug — fatal in both modes.
        const msg =
          `dws_ros2 resolved to '${resolvedOrigin}', ` +
          `expected it inside '${expectedRoot}'. ` +
          `A stale editable install or PYTHONPATH override is leaking in.`;
        this._log.error(`Interpreter identity check FAILED — ${msg}`);
        // In packaged mode make it fatal (previously it was just a warning,
        // which meant broken installs launched silently).
        return false;
      }

      this._log.info(`Interpreter identity check OK — dws_ros2 resolves to '${resolvedOrigin}'`);
      return true;

    } catch (err) {
      // execSync failure (interpreter not found, timeout, etc.)
      if (this._isDev) {
        this._log.error(`Interpreter identity check error: ${err.message}`);
        return false;
      }
      // In packaged mode, if we can't even run the interpreter, that's fatal.
      this._log.error(`Interpreter identity check error: ${err.message}`);
      return false;
    }
  }

  // ── ROS environment ────────────────────────────────────────────────────────

  _buildRosEnv() {
    const candidates = [
      '/opt/ros/jazzy/setup.bash',
      '/opt/ros/humble/setup.bash',
      '/opt/ros/rolling/setup.bash',
    ];

    for (const setup of candidates) {
      if (!fs.existsSync(setup)) continue;

      try {
        const py = `
import os
import json

keys = [
    "PYTHONPATH",
    "AMENT_PREFIX_PATH",
    "ROS_DISTRO",
    "ROS_VERSION",
    "ROS_PYTHON_VERSION",
    "COLCON_PREFIX_PATH",
    "PATH",
]

print(json.dumps({k: os.environ.get(k, "") for k in keys}))
`;

        const cmd =
          `source "${setup}" && python3 <<'EOF'\n` +
          py +
          `\nEOF`;

        const raw = execSync(
          ['bash', '-c', cmd].join(' '),
          {
            encoding: 'utf8',
            timeout: 15000,
            stdio: ['ignore', 'pipe', 'pipe'],
          }
        ).trim();

        const env = JSON.parse(raw);

        this._log.info(
          `Sourced ROS env from: ${setup} (distro: ${env.ROS_DISTRO})`
        );

        return env;

      } catch (err) {
        this._log.warn(`Could not source ${setup}: ${err.message}`);
      }
    }

    this._log.warn(
      'No ROS2 setup.bash found — backend will run without ROS environment'
    );

    return {};
  }

  _findRosSetup() {
    for (const p of [
      '/opt/ros/jazzy/setup.bash',
      '/opt/ros/humble/setup.bash',
      '/opt/ros/rolling/setup.bash',
    ]) {
      if (fs.existsSync(p)) return p;
    }
    return null;
  }

  // ── Crash restart ──────────────────────────────────────────────────────────

  _maybeCrashRestart(name, code, signal) {
    if (signal === 'SIGTERM' || signal === 'SIGKILL') return;

    const now = Date.now();
    this._crashes[name] = this._crashes[name]
      .filter(t => now - t < CRASH_WINDOW_MS);
    this._crashes[name].push(now);

    if (this._crashes[name].length >= MAX_CRASHES) {
      this._log.error(
        `${name} crashed ${this._crashes[name].length} times in ` +
        `${CRASH_WINDOW_MS / 1000}s — giving up`
      );
      this.emit('crashed', { name, count: this._crashes[name].length });
      return;
    }

    const delay = Math.min(
      RESTART_BASE_MS * Math.pow(2, this._crashes[name].length - 1),
      RESTART_MAX_MS
    );
    this._scheduleRestart(name, delay);
  }

  _scheduleRestart(name, delayMs) {
    this._log.info(`Scheduling restart of ${name} in ${delayMs}ms`);
    this._retimers[name] = setTimeout(() => {
      this._retimers[name] = null;
      if (!this._stopping) this._spawn(name).catch(err =>
        this._log.error(`${name} restart error: ${err.message}`)
      );
    }, delayMs);
  }

  // ── Status / helpers ───────────────────────────────────────────────────────

  _alive(name) {
    if (this._externallyManaged[name]) return true;
    const p = this._procs[name];
    return !!p && p.exitCode === null && p.signalCode === null;
  }

  _emitStatus() {
    this.emit('status', this.getStatus());
  }

  _ensureDirs() {
    for (const dir of [PID_DIR, LOGS_DIR]) {
      try { fs.mkdirSync(dir, { recursive: true }); } catch (_) {}
    }
  }

  _writePid(name, pid) {
    const p = path.join(PID_DIR, `${name}.pid`);
    try {
      if (pid == null) { fs.unlinkSync(p); } else { fs.writeFileSync(p, String(pid)); }
    } catch (_) {}
  }

  async _isSystemdManaged() {
    for (const name of PROCESS_NAMES) {
      const port = PROCESS_PORTS[name];
      if (port && await isPortOpen(port)) return true;
    }
    try {
      execSync('systemctl is-active --quiet dws.service dws@*.service',
        { stdio: 'ignore', timeout: 3000 });
      return true;
    } catch (_) {}
    return false;
  }
}

// ── Module-level helpers ────────────────────────────────────────────────────

function isPortOpen(port, timeout = 500) {
  return new Promise((resolve) => {
    const s = new net.Socket();
    s.setTimeout(timeout);
    s.on('connect', () => { s.destroy(); resolve(true);  });
    s.on('timeout',  () => { s.destroy(); resolve(false); });
    s.on('error',    () => { s.destroy(); resolve(false); });
    s.connect(port, '127.0.0.1');
  });
}

function waitForPort(port, timeoutMs) {
  const start    = Date.now();
  const deadline = start + timeoutMs;
  return new Promise((resolve) => {
    const check = async () => {
      if (await isPortOpen(port)) return resolve(true);
      if (Date.now() >= deadline)  return resolve(false);
      setTimeout(check, 1000);
    };
    check();
  });
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

module.exports = ProcessSupervisor;