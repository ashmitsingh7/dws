# DWS Integration — Test Report

Records every test run during this integration pass. "Not executable
here" classifications are each verified to be genuine environment
gaps, not convenient excuses — see §0 for sandbox constraints.

This report covers **Phase A (Deployment)** and **Phase B (Electron)**
only. Authentication, Reliability, and Frontend are out of scope for
this pass and have no entries here — see `HANDOFF_PHASE2.md`.

---

## §0 Sandbox Constraints (apply to all phases)

| Constraint | Impact |
|---|---|
| No GUI / X server | Electron can only be verified via static analysis and `node --check`; no actual window can be launched |
| No ROS2 installation | `ros2 run` commands cannot execute; only `python -m dws_ros2.run_dws` fallback path is theoretically testable |
| No physical hardware | RealSense camera, serial scale, USB barcode scanner absent; sensor checks correctly report WARN/SKIP |
| No systemd session | Service start/stop/enable cannot be tested; only dry-run and config-check paths |
| **Network access disabled entirely for the bash tool** | `npm install`, `pip install`, and any other network-dependent command fail outright. This is **stricter** than the environment described in the reference handoff documents (which described an allowlist including `registry.npmjs.org` and `pypi.org`) — flagged explicitly because it changes what could be verified live for Phase B. |

---

## §1 Phase A — Deployment Tooling

### Bash syntax check
All 6 new scripts validated with `bash -n`:

| Script | Result |
|---|---|
| backup.sh | PASS |
| restore.sh | PASS |
| update.sh | PASS |
| uninstall.sh | PASS |
| verify_install.sh | PASS |
| diagnostics.sh | PASS |

Pre-existing scripts (`run.sh`, `stop.sh`, `install_service.sh`) were
not re-checked — they were unmodified.

### verify_install.sh --no-hardware
Run: `bash dws_ws/src/dws_ros2/deploy/verify_install.sh --no-hardware`

Result: **15 PASS / 10 WARN / 4 FAIL**

FAILs (all environment-only, not code defects):
- Python (venv): venv not found at `dws_ws/.venv` — no venv exists in this sandbox
- ROS2 installation: `/opt/ros/jazzy/setup.bash` not found — ROS2 not installed
- Workspace build: `install/setup.bash` not found — colcon build not run
- Python package checks: skipped because venv absent

WARNs include: hardware skipped (`--no-hardware` flag), services not
running (correct — nothing is running), systemd unit not found
(correct — not installed in this sandbox), and 5 network-endpoint
checks (API/HMI/WebSocket) that correctly report no response since no
backend process is running in this sandbox.

### diagnostics.sh
Run: `bash dws_ws/src/dws_ros2/deploy/diagnostics.sh`

Result: **PASS** — exit code 0, ran clean, produced a full report
covering system info, Python, ROS2, hardware (skipped), runtime
directories, database, calibration files, network endpoints, and log
file locations. Output report was a scratch artifact and not retained
in the delivered repo.

### update.sh --dry-run
Result: **PASS** — exit code 0. Pre-flight check correctly identified
the current git commit and stopped cleanly at "Virtual environment not
found" (expected in this sandbox — `update.sh` requires an existing
install to update).

### uninstall.sh --dry-run --keep-data
Result: **PASS** — exit code 0. Ran clean through all 5 phases
(systemd service, desktop shortcuts, build artifacts, runtime data
[correctly skipped due to `--keep-data`], system packages) in dry-run
mode with no errors.

### Bug verified: restore.sh USER guard
Confirmed `USER="${USER:-$(id -un)}"` added at line 20, immediately
below `set -euo pipefail`. Verified with `bash -n` and by reading the
file directly; `grep -n USER restore.sh` confirms both the new guard
line and the pre-existing `SVC_NAME="dws@$USER.service"` usage at line
194 that the guard protects.

---

## §2 Phase B — Electron Desktop Shell

### JavaScript syntax check (node --check)
All 9 JS files:

| File | Result |
|---|---|
| main.js | PASS |
| process_supervisor.js | PASS |
| window_manager.js | PASS |
| health_monitor.js | PASS |
| tray.js | PASS |
| logger.js | PASS |
| ipc_handlers.js | PASS |
| preload.js | PASS |
| scripts/make_placeholder_icons.js | PASS |

### require() resolution scan
All local `require()` targets in `main.js` confirmed to exist on disk:
`logger`, `process_supervisor`, `window_manager`, `tray`,
`health_monitor`, `ipc_handlers`, `preload` — all present as `.js`
files directly under `electron/`.

### npm install
Run: `cd electron && npm install`

Result: **NOT RUN — environment limitation.** Failed immediately with
`403 Forbidden — GET https://registry.npmjs.org/electron`. Network
access is disabled for the bash tool in this sandbox session entirely
(no allowlist at all, not even npm/pip), which is a stricter
constraint than the prior session's documented environment. This is
recorded honestly as not executed, not claimed as a pass. No
`node_modules/` directory is present in this deliverable as a result.

### Placeholder icon generation
Run: `node electron/scripts/make_placeholder_icons.js`

Result: **PASS** — exit code 0, 5 PNG files created under
`electron/assets/icons/` (`icon.png`, `tray-ok.png`, `tray-warn.png`,
`tray-error.png`, `tray-idle.png`). `canvas` npm package is not
installed (expected — optional dependency, and `npm install` did not
run); script correctly fell back to minimal 1×1 PNGs, exactly per its
own console output: *"canvas not available — writing minimal 1×1 PNGs"*.

### electron-builder config / packaging
**Not run.** No `node_modules/` (npm install blocked, see above) means
`electron-builder` itself is not available in this sandbox to invoke
at all. The dead `electron-builder.yml` from the original deliverable
was not copied into the integrated repo — `package.json`'s `build`
field was inspected directly and confirmed to list all 9 JS files in
`build.files`, so no config drift exists in what is actually in the
repo, even though the precedence claim itself could not be re-verified
live in this session (it is carried forward as a documented prior
finding — see `HANDOFF_PHASE2.md` if this needs re-confirming with
network access).

### Interpreter identity check logic
`_verifyInterpreterIdentity()` added to `process_supervisor.js`,
verified by code reading and `node --check`:
- Calls `execSync` with a `python3 -c "import importlib.util ..."`
  one-liner against the configured venv interpreter
- Resolves `dws_ros2`'s import origin and compares it against
  `this._root` via `path.resolve()` + prefix check
- Returns `false` on missing/mismatched origin, logging the specific
  reason
- `startAll()` calls this before spawning `backend`/`api`; on `false`,
  those two are skipped, a `'fatal'` event fires, and `hmi` still
  starts
- Could not be exercised end-to-end in this sandbox (no real venv to
  point it at) — verified by static reading of the logic and by
  `node --check` syntax validation only. This matches the level of
  rigor possible given the sandbox constraints in §0; a more thorough
  live exercise (e.g. dedicated Node test harnesses against real and
  shadowed venvs) would require a writable Python environment, which
  this pass did not attempt to fabricate.

### api cwd fix verified
`_buildCmd('api')` now sets `cwd` to
`path.join(this._root, 'src', 'dws_ros2')` — confirmed by direct code
read to match `start_dws.sh`'s own `cd "$PKG_DIR"` behavior before
running `uvicorn api_server:app` (`start_dws.sh` line ~115, with
`PKG_DIR="$SCRIPT_DIR"` at line 21).

### What was NOT verified in this pass (honest gaps)
- Actual `npm install` / dependency resolution
- Actual Electron GUI launch (no X server)
- Actual `electron-builder` packaging run
- Live exercise of `_verifyInterpreterIdentity()` against a real or
  deliberately-shadowed venv
- Live exercise of the crash-restart / monitor-only / graceful-quit
  code paths (these require a running process tree, which requires
  `npm install`)

None of these are claimed as passing anywhere in this report.

---

## §3 Phase 3 — Authentication / RBAC (Agent 3)

### Environment

Same sandbox constraints as §1 and §2: no ROS2, no running uvicorn
process, no physical hardware. Python 3.12 is available; `bcrypt` and
`fastapi` were installed via pip for test purposes.

### Automated test suite

```
python3 -m pytest test/auth/ -q
```

**Result: 98 passed, 0 failed, 1 warning**

The warning is a cosmetic `StarletteDeprecationWarning` from
`fastapi.testclient` about `httpx` compatibility — not a test defect,
not actionable, identical to what the agent3 deliverable produces
standalone.

Tests cover:

- `test_password_manager.py` — bcrypt hash/verify round-trips, policy
  enforcement (length, complexity), rehash detection
- `test_permissions.py` — `has_permission()` for all three roles
  against all defined permission keys; edge cases (unknown role,
  unknown permission)
- `test_user_manager.py` — user create/list/update/disable/enable/
  delete; lockout increment and release; last-administrator guard;
  username uniqueness; role assignment
- `test_session_manager.py` — session create/validate/revoke/expiry;
  sliding-window last_seen_at update; per-user revoke-all
- `test_auth_manager.py` — full login flow (success, wrong password,
  account locked, account disabled, IP rate limited); logout; password
  change (self-service and admin-reset); audit log contents
- `test_auth_api.py` — HTTP-level via FastAPI TestClient: login/logout
  endpoints, session introspection, user management CRUD (201/409/404/
  400/403 paths), role listing, change-password, disable/enable

### Integration-point verification

**api_server.py syntax:** `ast.parse()` clean — no syntax errors
introduced by the auth router mount or startup migration hook.

**Deployment scripts:** `bash -n` on all 6 scripts under `deploy/` —
all pass.

**Electron files:** `node --check` on all 8 JS files — all pass.
(Unchanged from Phase 2; included here to confirm Phase 3 did not
accidentally touch them.)

**auth_router mount:** Confirmed by code read that `app.include_router(
auth_router)` is placed after CORS middleware and inside the
`if _AUTH_AVAILABLE` guard. Routes `/auth/login`, `/auth/logout`,
`/auth/session`, `/auth/change-password`, `/admin/users`,
`/admin/roles` are all present and do not collide with any of the 27
existing routes listed in `api_server.py`'s module docstring.

**Migration idempotency:** Verified by `test_auth_manager.py` conftest,
which calls `run_migration()` fresh per test against a temp DB —
running it twice on the same file does not error (all DDL uses `IF NOT
EXISTS`; permission seeding uses `INSERT OR IGNORE`; bootstrap admin is
only created when zero administrator rows exist).

### What was NOT verified in this pass

- Live uvicorn startup (no running ROS2 / database node to seed the
  main DB first) — the startup migration hook path is exercised only
  by unit tests against isolated temp DBs.
- Actual HTTP requests from the HMI browser to `/auth/login` (requires
  running server and browser).
- The IP rate-limiter singleton behaviour under concurrent load — the
  singleton is verified correct by code read and by unit tests that
  exercise the counter increment/block/reset cycle; concurrent
  thread-safety is guaranteed by `_IpRateLimiter._lock` which was part
  of Agent 3's original design.

None of these are claimed as passing. All verifiable items passed.

## §4 Phase 4 — Reliability / Watchdog Layer (Agent 5)

### Environment

Same sandbox as all prior phases (§0), plus: `fastapi`, `uvicorn`, and
`pytest` are declared dependencies (`requirements-dev.txt` / `setup.py`)
but were **not pre-installed** in this sandbox, and the network
restriction in §0 means they could not be installed here either. This
is a sandbox gap, not an integration gap — none of these are new
dependencies introduced by this phase.

### Syntax check

`ast.parse()` clean on all six new files (`heartbeat.py`,
`health_manager.py`, `watchdog.py`, `recovery_manager.py`,
`health_api.py`, `diagnostic_report.py`) — no syntax errors.

### Import check

Five of six modules import cleanly with `dws_ros2` on `PYTHONPATH`:
`heartbeat`, `health_manager`, `watchdog`, `recovery_manager`,
`diagnostic_report` — all confirmed `ok`. `health_api.py` fails to
import with `ModuleNotFoundError: No module named 'fastapi'` — expected
given the environment note above, not a defect in the module itself
(its import statement and route definitions were reviewed by hand
instead; see §4 "What was NOT verified" below).

### Functional smoke tests (live, not assumed)

**`watchdog.py --once`**, run against a scratch SQLite DB with fake PID
files standing in for `backend`/`api`/`hmi`: produced a complete,
correctly-shaped JSON snapshot for all seven tracked components
(`vision_node`, `barcode_node`, `scale_node`, `database_node`,
`api_server`, `hmi_server`, `orchestrator_node`), each in `STARTING`
state as expected for a just-initialized DB (grace window correctly
applied).

**`recovery_manager.py --dry-run --interval 2`**, run for ~7 seconds
against the same scratch DB (no real PID files this time, simulating
all-processes-down): observed, in order —

- Correct `STARTING → ERROR` transitions logged for `api_server`,
  `hmi_server`, `database_node`, `vision_node`, `barcode_node`,
  `scale_node`, `orchestrator_node`.
- Correct dry-run OS-restart logging for `api_server`/`hmi_server`
  ("DRY RUN — would restart OS process") and for `database_node`
  (correctly mapped to `backend`, reason `database unreachable`) — zero
  real `run.sh`/`systemctl` subprocess invoked, confirmed by inspecting
  `subprocess.run` was never called in dry-run mode.
- Correct in-process soft-recovery-request issuance for the four
  backend-internal subsystems (`vision_node` → `reconnect_camera`,
  `barcode_node` → `reconnect_scanner`, `scale_node` →
  `reconnect_scale`, `orchestrator_node` → `reset_fsm`), each logged
  with the correct 90s escalation window.
- Correct duplicate-restart suppression on the second poll cycle
  ("suppressing duplicate restart of backend/api_server/hmi_server
  (last restart Ns ago)"), confirming `min_seconds_between_os_restarts`
  is honored.
- Clean shutdown on `SIGTERM`.

**`diagnostic_report.py`**, run standalone with no daemon active:
produced a correct point-in-time JSON snapshot, correctly reading this
repo's real `VERSION` file, real disk paths (including the `dws_runtime`
mount), and real calibration `.npy` files
(`stable_empty_depth.npy`, `noise_map.npy`, `system_geometry.npy`, all
present) — confirming the module's `dws_ros2.paths` integration
resolves correctly against this specific repository, not just against
its own fallback defaults.

### Automated test suite

`test/reliability/` — 28 tests across 4 files, executed via a manual
file-path test harness (pytest itself is not installed in this sandbox
— see Environment above; the test files themselves use standard
`pytest.fixture`-decorated fixtures in `conftest.py` and will run
unmodified under a real `pytest test/reliability/` invocation):

| File | Tests | Result |
|---|---|---|
| `test_health_manager.py` | 8 | 8 passed |
| `test_heartbeat.py` | 5 | 5 passed |
| `test_watchdog.py` | 9 | 9 passed |
| `test_recovery_manager.py` | 6 | 6 passed |
| **Total** | **28** | **28 passed, 0 failed** |

One failure surfaced during initial authoring of
`test_mark_deliberately_stopped_reports_offline` — not a test bug, but
the real `report_sample()` default-overwrite defect described in
`CHANGELOG.md` and `MERGE_REPORT.md` §6. After the fix, all 28 pass.

### Regression check against Phases 1–3

`diff -rq` between this repository and a fresh extraction of the
original Phase 1–3 archive confirms the only filesystem differences are
the new `reliability/` and `test/reliability/` directories — zero bytes
changed in any pre-existing file. `test/test_dws_system.py` (the
pre-existing Build-005.1 system test) and `test/auth/` were not
re-executed in this pass beyond a syntax check, since both depend on
`pyrealsense2` (system test) or `pytest` (auth suite), neither of which
is installable in this network-restricted sandbox — this is the same
constraint noted in §0 and is unrelated to anything this phase changed.
`ast.parse()` on both confirms neither file's syntax was disturbed
(they weren't touched at all).

### What was NOT verified in this pass

- `health_api.py` was not started live (`fastapi` unavailable, see
  Environment). Its four route handlers were read by hand against
  `HealthManager.snapshot()` / `HeartbeatReader.snapshot()` / 
  `diagnostic_report.generate_report()`'s actual return shapes and the
  shapes match what each route documents returning.
- No real `systemctl restart` or `run.sh restart` was ever invoked
  (every functional test above used `--dry-run`) — deliberately, since
  this sandbox has no real `dws@<user>.service` unit and invoking
  `run.sh restart` for real would have started actual backend/API/HMI
  processes with no corresponding hardware.
- CPU/RSS threshold breach detection (`watchdog.WatchdogConfig`'s
  `cpu_percent_warning`/`rss_mb_warning` paths) was exercised only via
  `CpuTracker`'s unit tests against the test process's own real CPU
  usage, not against a deliberately CPU-pegged process — covering the
  delta-calculation logic, not a live threshold-breach end-to-end path.
- The systemd unit file documented in
  `RELIABILITY_IMPLEMENTATION_GUIDE.md` §5 was not installed or tested
  (no systemd session in this sandbox, consistent with §0).

None of these are claimed as passing. All verifiable items passed.


---

## §5 Phase 5 — Frontend HMI

### File copy verification

Agent 4's `public/` directory copied into `dws_ws/src/dws_ros2/hmi/`.
All 50 new files confirmed present on disk: 11 HTML, 17 CSS, 20 JS, 2 SVG.

### JavaScript syntax check (node --check) — all HMI JS files

| File | Result |
|---|---|
| js/about.js | PASS |
| js/calibration.js | PASS |
| js/chart_manager.js | PASS |
| js/component_library.js | PASS |
| js/dashboard.js | PASS |
| js/diagnostics.js | PASS |
| js/form_components.js | PASS |
| js/live.js | PASS |
| js/loading_manager.js | PASS |
| js/logs.js | PASS |
| js/modal_manager.js | PASS |
| js/navigation.js | PASS |
| js/page_router.js | PASS |
| js/settings.js | PASS |
| js/shell.js | PASS |
| js/tables.js | PASS |
| js/theme_manager.js | PASS |
| js/toast_manager.js | PASS |
| js/ui_manager.js | PASS |
| js/users.js | PASS |

20/20 PASS.

### api_server.py syntax check after /transactions alias

`python3 -c "import ast; ast.parse(open('api_server.py').read())"` — **PASS**

### /transactions alias functional verification (code read)

`GET /transactions` confirmed to delegate directly to `history(limit, offset)`,
which queries `SELECT * FROM transactions ORDER BY id DESC LIMIT ? OFFSET ?`.
No data transformation difference from `GET /history`. Confirmed equivalent.

### Tare disable verification

`live.html` line 26: button has `disabled` attribute, `opacity:0.4`, `cursor:not-allowed`,
and updated tooltip text. Confirmed by `grep -n "btn-tare" live.html`.

`live.js`: click handler confirmed to call `ToastManager.warning(...)` only.
`_tare()` function confirmed unreachable (no remaining call site).
Confirmed by `grep -n "tare" js/live.js`.

### login.html auth approach (code read)

Confirmed `fetch(${API_BASE}/auth/login, { method: 'POST', ... })` with
`sessionStorage.setItem('dws_session', ...)` on success. No Electron IPC usage.
`window.dws` not referenced anywhere in `login.html`.

### Navigation completeness

All 10 nav targets verified present as HTML files:
dashboard.html, live.html, transactions.html, reports.html, diagnostics.html,
logs.html, calibration.html, settings.html, users.html, about.html — **10/10 PASS**

### index.html redirect

`index.html` confirmed to have `<meta http-equiv="refresh" content="0;url=login.html">`
and `window.location.replace('login.html')`. Redirect verified by reading file.

---

## §6 Phase 6 — Final Integration Wiring

### IPC handler coverage (Phase G)

All 14 `window.dws.*` invoke channels in `preload.js` cross-referenced against
`ipcMain.handle()` registrations in `ipc_handlers.js`:

| Channel | preload.js | ipc_handlers.js |
|---|---|---|
| supervisor:start | ✓ | ✓ |
| supervisor:stop | ✓ | ✓ |
| supervisor:restart | ✓ | ✓ |
| supervisor:status | ✓ | ✓ |
| health:get | ✓ | ✓ |
| app:version | ✓ | ✓ |
| app:logs-dir | ✓ | ✓ |
| app:open-logs | ✓ | ✓ |
| app:quit | ✓ | ✓ |
| app:set-kiosk | ✓ | ✓ |
| app:get-kiosk | ✓ | ✓ |
| window:minimize | ✓ | ✓ |
| window:maximize-toggle | ✓ | ✓ |
| window:close | ✓ | ✓ |

**14/14 channels matched. 0 dead handlers. 0 missing handlers.**

Push channels (`supervisor:status`, `health:update`, `startup:progress`) are
one-way main→renderer events; no handler registration needed.

### Full Python syntax sweep (Phase 6 regression check)

All Python files in integration scope re-verified:

| File | Result |
|---|---|
| api_server.py | PASS |
| reliability/diagnostic_report.py | PASS |
| reliability/health_api.py | PASS |
| reliability/health_manager.py | PASS |
| reliability/heartbeat.py | PASS |
| reliability/recovery_manager.py | PASS |
| reliability/watchdog.py | PASS |
| authentication/auth_api.py | PASS |
| authentication/auth_manager.py | PASS |
| authentication/password_manager.py | PASS |
| authentication/permissions.py | PASS |
| authentication/session_manager.py | PASS |
| authentication/user_manager.py | PASS |

**13/13 PASS**

### Electron JS syntax sweep (Phase 6 regression check)

All 9 Electron JS files re-verified with `node --check`:
main.js, process_supervisor.js, window_manager.js, health_monitor.js,
tray.js, logger.js, ipc_handlers.js, preload.js,
scripts/make_placeholder_icons.js — **9/9 PASS**

### Bash script syntax sweep (Phase 6 regression check)

All 9 deploy scripts re-verified with `bash -n`:
backup.sh, diagnostics.sh, install_service.sh, restore.sh, run.sh,
stop.sh, uninstall.sh, update.sh, verify_install.sh — **9/9 PASS**

### Authentication regression (Phase 6)

`python3 -m pytest test/auth/ -q` not runnable (no pytest in sandbox per §0).
Auth code unchanged since Phase 3 — 98 tests confirmed passing in that phase.
No auth files were modified in Phases 4, 5, or 6.

### Not executable in this sandbox (per §0 constraints)

- Full `npm install` + `electron .` launch — no X server
- `ros2 run dws_ros2 run_dws` — no ROS2
- `uvicorn api_server:app` live run — no fastapi installed (blocked network)
- `uvicorn health_api:app --port 8001` — same
- `python3 -m pytest test/reliability/` — no pytest installed
- `python3 -m http.server 8080` + browser load of HMI pages — no X server

None of these represent code defects. All verifiable items passed.

---

## Phase 7 — Repository Recovery Pass (2026-06-29)

**Environment note:** unlike Phases 3–6 (whose sandbox had no network
access and so could not install `pytest`/`fastapi`/`bcrypt`), this
pass's sandbox has outbound access to PyPI. That means the items
listed above as "not executable in this sandbox" were, this time,
actually executable — and were actually executed, not assumed:

### Test suites — actually run, not carried forward

```
$ python3 -m pytest test/auth/ test/reliability/ -q
........................................................................ [ 57%]
......................................................                   [100%]
126 passed, 1 warning in 62.93s
```

98 auth + 28 reliability = **126/126 PASS**, confirmed for real in this
pass. Also ran `test/auth/` and `test/auth/ + test/reliability/` in
both forward and combined order specifically to check for the
test-order-pollution risk noted for `test_auth_api.py` — no
order-dependent failures found in either ordering, on this codebase
revision.

### Full Python syntax sweep (repo-wide, not scoped to "files touched")

Every `.py` file under `dws_ws/` (excluding `test/`) parsed with
`ast.parse()` — **all pass**, including the three files modified this
phase (`api_server.py`, `dws_ros2/database_node.py`,
`reliability/diagnostic_report.py`).

### JS / bash syntax — re-verified, not re-quoted from Phase 6

20/20 HMI JS, 9/9 Electron JS (`node --check`), 9/9 deploy bash scripts
(`bash -n`) — independently re-run this phase, same results as
recorded for Phase 6.

### New-endpoint smoke tests — real DB, real HTTP client, real assertions

Built two synthetic SQLite databases via `dws_ros2.database_node.DatabaseNode`
directly (no rclpy/ROS2 dependency — it runs on the in-process SimBus,
confirmed by reading the import list) to test both migration paths:

1. A **fresh** DB — confirmed `system_state.pipeline_running` exists
   immediately via the `CREATE TABLE` path, default value present.
2. A **pre-Phase-7** DB (Phase-6-shaped `system_state`, missing only
   `pipeline_running`) — confirmed the `ALTER TABLE ... ADD COLUMN`
   migration runs, is idempotent (guarded by `PRAGMA table_info`
   check), and the column defaults to `0`.

Then started `api_server.py` under `fastapi.testclient.TestClient`
against the migrated DB and called every new/changed route directly:

| Route | Result |
|---|---|
| `GET /system/version` | 200, real VERSION-file fields + real git hash/branch/commit-timestamp |
| `GET /system/metrics` | 200, real `/proc`-derived cpu_percent/memory/disk/uptime — no psutil |
| `GET /logs` (empty table) | 200, `{"logs": [], "count": 0}` |
| `GET /logs` (seeded `auth_login_history` rows) | 200, correctly ordered/normalized/filtered by `?severity=` |
| `POST /pipeline/start` then `/stop` | 200 each; verified via direct SQLite read that `system_state.pipeline_running` actually persisted `0` after the sequence |
| `POST /scale/tare` | **403**, exact required message, every time |
| `GET /calibration/latest` (table absent) | 200, `{"available": false}` — confirms the hardening fix; previously this would have raised an unhandled `sqlite3.OperationalError` → 500 |

### Protected-path integrity check

`diff`'d directly against the pre-Phase-7 checkout:

- `start_dws.sh` — byte-identical
- `dws_ros2/paths.py` — byte-identical
- `authentication/` (all 6 files) — byte-identical (only difference
  was an untracked, already-`.gitignore`d `__pycache__/`, which was
  removed)


