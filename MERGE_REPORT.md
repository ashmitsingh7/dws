# DWS — Merge Report

Tracks every integration decision made while merging independently-built
agent deliverables into `dws_finalv7`. The original repository is always
treated as the source of truth; agent code is adapted to it, not the
reverse. This report currently covers **Phase A (Deployment)** and
**Phase B (Electron)** only — Authentication, Reliability, and Frontend
are out of scope for this pass and are intentionally not present in this
repository (see `HANDOFF_PHASE2.md`).

---

## §1 Scope of This Pass

| Phase | Subsystem | Status |
|---|---|---|
| A | Deployment tooling (Agent 1) | Integrated, tested, committed |
| B | Electron desktop shell (Agent 2) | Integrated, tested, committed |
| — | Authentication / RBAC (Agent 3) | **Out of scope — not started** |
| — | Reliability (Agent 5) | **Out of scope — not started** |
| — | Frontend (Agent 4) | **Out of scope — not started** |
| — | Final wiring | **Out of scope — not started** |

---

## §2 Phase A — Deployment Tooling

### Source
`DWS_MASTER_PACKAGE/agent1_deployment/`

### Destination
`dws_ws/src/dws_ros2/deploy/` (new files alongside pre-existing
`run.sh`, `stop.sh`, `install_service.sh`, `dws.service`), plus
`DEPLOYMENT_RECOVERY.md` at the repo root.

### Files added
`backup.sh`, `restore.sh`, `update.sh`, `uninstall.sh`,
`verify_install.sh`, `diagnostics.sh`, `diagnostic_report_example.txt`.

### Conflict analysis
None. The deliverable independently derived the correct repo paths
(`dws_runtime/`, `dws_ws/.venv`), port numbers (8000, 8080, 8765,
8090), database filename (`dws_data.db`), and systemd unit naming
(`dws@<user>.service`, matching the pre-existing `dws.service` template
and `install_service.sh`) before being written. No renaming, no
re-routing, no architecture changes were required.

### Bug found and fixed
`restore.sh` used `$USER` (in `SVC_NAME="dws@$USER.service"`) without
the `USER="${USER:-$(id -un)}"` guard that the other four new scripts
already had. Under `set -u` (the script uses `set -euo pipefail`),
this would crash with `USER: unbound variable` in any shell where
`$USER` is not exported — a real risk in some non-interactive, cron,
or sudo contexts. Fixed by adding the same guard, on line 20, directly
beneath `set -euo pipefail`.

### Verification performed (all live, not assumed)
See `INTEGRATION_TEST_REPORT.md` §1 for full output. Summary:
- `bash -n` on all 6 new scripts: all PASS
- `verify_install.sh --no-hardware`: 15 PASS / 10 WARN / 4 FAIL — every
  FAIL traced to a genuine sandbox gap (no venv, no ROS2 install, no
  colcon build — none are code defects)
- `diagnostics.sh`: ran clean, produced a full report
- `update.sh --dry-run`: ran clean, stopped at the expected "no venv"
  pre-flight check
- `uninstall.sh --dry-run --keep-data`: ran clean through all 5 phases

### Documentation
Added an "Operations & Recovery Scripts" subsection to `README.md` §9,
between the systemd method and the HMI section, describing each
script's purpose and flags in one table.

### Open items
None. Nothing further required for this subsystem.

---

## §3 Phase B — Electron Desktop Shell

### Source
`DWS_MASTER_PACKAGE/agent2_electron/`

### Destination
New `electron/` directory at the repo root, as a sibling of `dws_ws/`
and `dws_runtime/` — required by `package.json`'s
`extraResources: [{from: "../dws_ws", ...}]` and `main.js`'s dev-mode
backend-root resolution (`path.resolve(__dirname, '..', 'dws_ws')`).
Both were read directly, not assumed, before this placement was chosen.

### Files added
`main.js`, `process_supervisor.js`, `window_manager.js`,
`health_monitor.js`, `tray.js`, `logger.js`, `ipc_handlers.js`,
`preload.js`, `splash.html`, `package.json`, `README.md`,
`ELECTRON_ARCHITECTURE.md`, `ELECTRON_IMPLEMENTATION_CHECKLIST.md`,
`electron/scripts/{build.sh,launch.sh,make_placeholder_icons.js}`,
and 5 generated placeholder icons under `electron/assets/icons/`.

### Architectural conformance
Electron is strictly a launcher/supervisor here, not a fourth backend
process and not a replacement for any existing one. `window_manager.js`
loads the URL `http://localhost:8080` only — confirmed by reading the
code — never a specific file, so it has zero awareness of what HTML is
actually served there. The three-process architecture
(`run_dws.py` / `api_server.py` / HMI static server) is unchanged;
`process_supervisor.js` merely spawns and monitors the same three
processes that `start_dws.sh` would otherwise start by hand.

### Conflicts found and resolved

**1. No venv-shadowing defense in `process_supervisor.js`.**
`start_dws.sh` (pre-existing, not modified) already defends against a
documented real incident: a stale `.venv` from a different checkout
silently shadowing the correct one, with `api_server.py` then running
against the wrong code with no error. The delivered
`process_supervisor.js` had no equivalent check before spawning
`backend`/`api` with the configured venv interpreter.

*Resolution:* added `_verifyInterpreterIdentity()`, called once at the
top of `startAll()`. It runs
`importlib.util.find_spec('dws_ros2').origin` through the same
interpreter `ProcessSupervisor` is about to use, resolves it with
`path.resolve()`, and checks it falls inside `this._root`. On mismatch:
`backend` and `api` are not spawned; a new `'fatal'` event is emitted
and handled in `main.js` with a `dialog.showMessageBox` call that
mirrors the existing `'crashed'` handler's structure exactly (same
button layout, same "View Logs" affordance); `hmi` is still started,
since the static HTTP server for the operator UI has no `dws_ros2`
import surface and isn't subject to this failure mode.

**2. `api` process working directory was wrong.**
`_buildCmd('api')` set `cwd` to
`path.join(this._root, 'src', 'dws_ros2', 'dws_ros2')` — one level too
deep. `api_server.py` actually lives at
`dws_ws/src/dws_ros2/api_server.py`, a sibling of the `dws_ros2/`
Python package directory, not inside it. Running `uvicorn
api_server:app` from the wrong directory would have failed outright.

*Resolution:* changed `apiSrc` to `path.join(this._root, 'src',
'dws_ros2')`. Cross-checked against `start_dws.sh` line ~115
(`cd '$PKG_DIR'` before `uvicorn api_server:app ...`), where
`PKG_DIR="$SCRIPT_DIR"` is the script's own location — confirming the
two now agree.

**3. Drifted, duplicate `electron-builder.yml`.**
The delivered package included both `package.json`'s `build` field
and a standalone `electron-builder.yml`, and the two had drifted: the
YAML's `files` whitelist was missing `logger.js` and `ipc_handlers.js`,
both required by `main.js` at startup (`require('./logger')`,
`require('./ipc_handlers')`).

*Resolution:* the YAML was not copied into the integrated repo.
`electron-builder`'s actual default config-precedence (`package.json`
"build" field takes effect when present, per electron-builder's own
documentation) makes the YAML dead weight that risks silently going
stale again. `package.json`'s `build.files` list was confirmed to
already include all 9 JS files correctly — no further fix needed
there.

**4. Packaging script layout.**
`build.sh`, `launch.sh`, and `make_placeholder_icons.js` were delivered
flat in the package root, but each one's own usage header
(`make_placeholder_icons.js`: "Run: node scripts/make_placeholder_icons.js")
and `package.json`'s `"make-icons": "node scripts/make_placeholder_icons.js"`
entry already assumed an `electron/scripts/` layout.

*Resolution:* moved all three into `electron/scripts/` to match what
the rest of the deliverable already expected — a packaging omission
in how the files were delivered, not a design issue.

### Verification performed
See `INTEGRATION_TEST_REPORT.md` §2 for full detail. Summary:
`node --check` PASS on all 9 JS files; a manual `require()`-resolution
scan confirmed every local import target in `main.js` exists on disk;
placeholder icon generation ran and produced the expected 5 PNGs with
the documented 1×1 fallback (no `canvas` package). **`npm install`
could not be run** — network access is disabled for the bash tool in
this sandbox session, unlike the environment described in the
reference handoff documents. This is recorded as an environment
limitation in `INTEGRATION_TEST_REPORT.md`, not claimed as a pass.
GUI launch (no X server) and an actual `electron-builder` packaging
run (requires `node_modules`, which requires the blocked `npm install`)
were likewise not attempted.

### Open items
- `node_modules/` is absent from this deliverable because `npm install`
  could not run in this sandbox. The repo is otherwise ready for it —
  re-run `npm install` inside `electron/` in an environment with
  registry access, then re-verify with `node --check` (already passing
  against the source) and, if an X server is available, an actual
  `npm start`.
- Placeholder icons under `electron/assets/icons/` are minimal 1×1
  PNGs and must be replaced with real artwork before any production
  package build (`build.sh` / `electron-builder`).

---

## §4 Open Conflicts (carried forward, not for this pass)

These are flagged for whoever picks up Authentication, Reliability, or
Frontend next — see `HANDOFF_PHASE2.md` for the full briefing. Listed
here only so this report is a complete index of everything known so
far; none of these were investigated or acted on in this pass.

| # | Issue | Status |
|---|---|---|
| 1 | Agent 4 frontend expects several non-existent backend endpoints | Not investigated in this pass |
| 2 | `/scale/tare` — orchestrator forbids software tare (legal-metrology constraint, `orchestrator_node.py` ~lines 81-82, ~2059) | Not investigated in this pass; flagged for whoever integrates the frontend |
| 3 | Possible Electron↔Auth IPC seam (`AuthWindow`, `auth:login`) | Not investigated — Authentication is out of scope for this pass entirely |
| 4 | Agent 5 reliability vs pre-existing `health_monitor.py` (both backend's `dws_ros2/health_monitor.py` and Electron's own renderer-side `health_monitor.js` exist — names overlap by coincidence, different layers, not yet cross-checked) | Not investigated in this pass |

---

## §5 Phase 3 — Authentication / RBAC (Agent 3)

### Source examined

`DWS_MASTER_PACKAGE/agent3_authentication/` — six Python modules in
`authentication/`, two database files in `database/`, seven test files
in `tests/`, plus `AUTH_API.md` and `AUTHENTICATION_ARCHITECTURE.md`.

### Integration approach

`authentication/` was copied as a sibling of `api_server.py` at
`dws_ws/src/dws_ros2/` — the location required by its relative import
structure (`from auth_manager import …` inside `auth_api.py`). The
`database/` directory landed at the same level. Test files were placed
under the existing `test/auth/` subdirectory to match the singular
`test/` convention already present in the repo.

`auth_router` was mounted in `api_server.py` with `app.include_router()`,
and `run_migration()` is called from a `@app.on_event("startup")` handler.
Both are inside a try/except so that a missing authentication package
causes a startup warning rather than a crash.

### Conflicts found

**Conflict 1 — IP rate-limiter not process-wide**

The rate-limiter bug is documented in the `auth_manager.py` docstring
itself: "instantiate one AuthManager per process and reuse it across
requests, not one per request." However, `auth_api.py` constructed a
new `AuthManager(conn)` inside every route body and inside
`_require_principal()`. Each construction created a fresh
`_IpRateLimiter()`, silently resetting all IP failure counters between
requests.

*Resolution:* `_process_ip_limiter` module-level singleton added to
`auth_api.py`. `_make_auth_manager(conn)` factory function replaces all
`AuthManager(conn)` calls and immediately sets `am._ip_limiter =
_process_ip_limiter`. Zero behaviour change for the happy path; the
rate-limit defence now actually accumulates across requests as intended.

**Conflict 2 — Test DB isolation broken by `dws_ros2.paths` import**

Agent 3's test fixture monkeypatches `DWS_DB_PATH` and re-imports
`auth_api` per test. This worked when `dws_ros2` was not on `sys.path`
(the original standalone `agent3_authentication/` checkout). In the
integrated repo, `dws_ros2` is importable, so `auth_api`'s module-level
`from dws_ros2.paths import DB_PATH` always resolved to the production
path, ignoring the monkeypatched env var and causing all tests that
require their own isolated DB to fail with `401 Invalid credentials`
(wrong DB, admin account not there).

*Resolution:* `_get_conn()` now resolves the path at call time: env var
`DWS_DB_PATH` first (enables test isolation), then `dws_ros2.paths.DB_PATH`
(production default), then module-level fallback. One-function change,
no behavioural impact on production.

**Conflict 3 — Test sys.path depth mismatch**

Agent 3's `tests/` directory is a direct sibling of `authentication/`
and `database/`, so path setup used `dirname(dirname(__file__))` to
climb back to the package root. In the integrated repo, test files sit
at `test/auth/`, two levels below the package root, not one.

*Resolution:* `conftest.py` was updated to climb two levels instead of
one (`dirname(dirname(dirname(__file__)))`). `test_auth_api.py` was
updated with the same fix; remaining test files already used an
equivalent three-`dirname` expression that happened to be correct after
one extra level was added by moving them into `test/auth/`.

### Verification performed

`python3 -m pytest test/auth/ -q` — **98 passed, 0 failed**, 1 warning
(cosmetic httpx deprecation from starlette's TestClient; not a test
defect). Full runtime verification (uvicorn startup, actual login over
HTTP) was not possible in this sandbox (no running ROS2 system for the
database node to have seeded the main DB), but the migration unit tests
cover the full lifecycle: schema creation, permission seeding, bootstrap
admin creation, login, logout, session validation, RBAC enforcement,
account lockout, password change, and admin user management.

### §5 open items carried forward

Conflicts 1–4 from §4 are now resolved or superseded as follows:

| # | Issue | Status |
|---|---|---|
| 1 | Agent 4 frontend expects non-existent endpoints | Still open — Phase 5 work |
| 2 | `/scale/tare` legal-metrology constraint | Still open — Phase 5 work |
| 3 | Electron↔Auth IPC seam | Tentatively resolved: Agent 4's `login.html` uses direct `fetch('/auth/login')` over HTTP — no Electron IPC changes needed. Confirm by reading login.html before acting. |
| 4 | Agent 5 vs pre-existing `health_monitor.py` | **Resolved this pass — see §6.** |

## §6 Phase 4 — Reliability / Watchdog Layer (Agent 5)

### Source examined

`DWS_MASTER_PACKAGE/agent5_reliability/` — six Python modules
(`heartbeat.py`, `health_manager.py`, `watchdog.py`,
`recovery_manager.py`, `health_api.py`, `diagnostic_report.py`), plus
`RELIABILITY_ARCHITECTURE.md` and
`RELIABILITY_IMPLEMENTATION_GUIDE.md`. Item §4.4's open question — how
this layer relates to the pre-existing `health_monitor.py` — is
answered directly in the source material itself (see
`RELIABILITY_ARCHITECTURE.md` §2): the new layer is a second,
independent process that cross-checks `health_monitor.py`'s
`health_status` row rather than replacing it, specifically to cover the
case where the main backend process is wedged and that row would be
honest-but-stale.

### Integration approach

`reliability/` was copied as a new top-level subdirectory of
`dws_ws/src/dws_ros2/`, sibling to `authentication/`, `database/`, and
`dws_ros2/` — its own design (RELIABILITY_IMPLEMENTATION_GUIDE.md §1)
already specified this exact location. Test files were placed under
`test/reliability/`, matching the `test/auth/` convention already
present in the repo (and deliberately *without* an `__init__.py` in
either `test/` or `test/reliability/`, since adding one would have
changed `setup.py`'s `find_packages()` output and started packaging
`test`/`test.reliability` for installation — they were never packaged
before and Agent 5's brief did not call for that to change).

Per the architecture document's own Sprint-1 scope (§8), nothing was
wired into `setup.py`'s `console_scripts`, `package.xml`, or
`dws_params.yaml` — the six modules run as standalone scripts from the
source checkout, exactly as designed, and exactly as `config_loader.py`
already supports being invoked standalone elsewhere in this codebase.

### Conflicts found

No conflicts with existing files — this phase touched zero pre-existing
files. One internal bug in the delivered Agent 5 source was found
during verification:

**Bug 1 — `mark_deliberately_stopped()` overwritten by the next
watchdog poll**

`HealthManager.report_sample(..., deliberately_stopped: bool = False)`
unconditionally wrote its `deliberately_stopped` argument into the
component's persisted state every call. `watchdog.py`'s three
`report_sample()` call sites never pass that argument, so every ~5s
poll cycle was implicitly passing `False` and clobbering whatever an
operator had just set via `mark_deliberately_stopped(component, True)`
— the documented planned-maintenance workflow
(RELIABILITY_IMPLEMENTATION_GUIDE.md §7) would silently flip back to
reporting `ERROR` instead of `OFFLINE` within one poll interval.

*Resolution:* default changed to `None`; the field is now only
overwritten when the caller passes an explicit value, matching the
"`None` = no opinion this cycle" convention every other `report_sample`
parameter already follows (see the method's own docstring, which this
fix also updates). Verified no call site relied on the old
always-overwrite default.

### Verification performed (all live, not assumed)

- `watchdog.py --once` run against a scratch SQLite DB with fake PID
  files — produced a correct, fully-populated JSON health snapshot for
  all seven components.
- `recovery_manager.py --dry-run --interval 2` run for several cycles
  against the same scratch DB — confirmed correct `STARTING → ERROR`
  transitions on simulated process death, correct dry-run logging
  ("would restart OS process: …") with zero real `run.sh`/`systemctl`
  invocation, correct in-process soft-recovery-request issuance for
  backend-internal subsystems, and correct duplicate-restart
  suppression on a second cycle within `min_seconds_between_os_restarts`.
- `diagnostic_report.py` run standalone — produced a correct
  point-in-time snapshot picking up this repo's real calibration files
  and disk paths.
- `python3 -m pytest test/reliability/ -q` (run via a manual file-path
  test harness in this sandbox, since outbound network access — and
  therefore `pip install pytest`/`fastapi` — is unavailable here; both
  are pre-existing declared dependencies in `requirements-dev.txt` /
  `setup.py`, not new ones) — **28 passed, 0 failed**.
- `health_api.py` could not be started in this sandbox (`fastapi` not
  installable offline) — its read-only endpoint logic was reviewed by
  hand against `health_manager.py`'s and `heartbeat.py`'s real return
  shapes and matches the documented `/health`, `/health/full`,
  `/health/history`, `/diagnostics` contracts; live verification is
  flagged as an outstanding item for an environment with network
  access — see `INTEGRATION_TEST_REPORT.md` and `HANDOFF_PHASE4.md`.
- Confirmed via `diff -rq` against the original Phase 1–3 archive that
  zero pre-existing files were modified, added, or removed by this
  pass — the only filesystem changes are the new `reliability/` and
  `test/reliability/` directories.

### Documentation

`CHANGELOG.md` Phase 4 entry added. `INTEGRATION_TEST_REPORT.md`
updated with the full reliability-layer test run.
`HANDOFF_PHASE4.md` created.

### Open items

See `HANDOFF_PHASE4.md` for the full list. Headline items: `health_api.py`
needs a live run once `fastapi` can be installed; no in-process consumer
yet exists for `reliability_recovery_requests` (Sprint 2 work per the
architecture doc's own §8, requires touching `run_dws.py`/
`orchestrator_node.py`, explicitly out of this phase's scope); no
heartbeat call sites yet exist inside `vision_node.py`/`scale_node.py`/
`barcode_node.py`/`orchestrator_node.py` (same reason).

---

## §7 Phase 5 — Frontend HMI (Agent 4)

### Decision: Option 1 — copy into hmi/

`agent4_frontend/public/` contents were copied verbatim into
`dws_ws/src/dws_ros2/hmi/`. This preserves the HMI server's existing
`python3 -m http.server 8080` invocation (serving `hmi/`) and requires
zero changes to `start_dws.sh` or Electron. The alternative (repointing
`start_dws.sh`'s HMI server `cwd`) was rejected because it would modify
the hardened startup script and create a dependency on the external
`agent4_frontend/` path.

### Auth IPC gap — resolved without Electron changes

The deferred `AuthWindow`/`auth:login` IPC seam documented in Phase B
(Agent 2 Electron) was confirmed closed by reading `login.html`: all
auth calls are `fetch` calls to `http://<hostname>:8000/auth/login` with
token stored in `sessionStorage`. No Electron-layer auth involvement.

### /transactions alias

`api_server.py` received one new route: `GET /transactions` delegates
directly to the `history()` handler. Named `transactions_alias` to avoid
shadowing. This is the only backend file modified by this phase.

### Tare disable

Two-layer disable in `live.html` (HTML `disabled` attribute, tooltip
updated to explain the constraint) and `live.js` (click handler replaced
with a ToastManager warning). The `_tare()` function was left in place
(dead code) rather than deleted to preserve the function boundary; the
click handler no longer calls it.

### Endpoints left as graceful-degradation (not implemented)

| Frontend calls | Reason not implemented |
|---|---|
| `GET /system/state` | No corresponding backend node/data |
| `GET /system/metrics` | Reliability layer has `/diagnostics` at :8001; no proxy added this phase |
| `GET /calibration/status` | No calibration state machine in api_server |
| `POST /calibration/start` | Calibration is operator-initiated; no backend handler |
| `POST /calibration/compute` | Same |
| `POST /calibration/save` | Same |
| `POST /pipeline/{action}` | No pipeline-control API in orchestrator |

All of the above already have try/catch in the frontend JS with silent or
"Unavailable" UI fallback.

### Files changed this phase

- `dws_ws/src/dws_ros2/hmi/` — 50 new files (11 HTML, 17 CSS, 20 JS, 2 SVG)
- `dws_ws/src/dws_ros2/hmi/index.html` — replaced with meta+JS redirect to login.html
- `dws_ws/src/dws_ros2/api_server.py` — one new route added (`/transactions`)
- `dws_ws/src/dws_ros2/hmi/live.html` — tare button disabled
- `dws_ws/src/dws_ros2/hmi/js/live.js` — tare click handler neutered
- `dws_ws/src/dws_ros2/hmi/calibration.html` — page title/subtitle updated per Phase J

No other files touched.

---

## §8 Phase 6 — Final Integration Wiring

### Summary

Phase 6 is an audit-and-cement pass across all five subsystems. No new
files were created beyond this documentation; all code changes were
already captured in Phases 1–5.

### Phase B audit — Electron ↔ Frontend

`window_manager.js` HMI_URL is `http://localhost:8080` — matches the
`python3 -m http.server 8080` invocation in `process_supervisor.js`.
`index.html` now redirects to `login.html`, so the Electron window
presents the login page on first load. All 14 `window.dws` IPC channels
in `preload.js` have corresponding `ipcMain.handle()` registrations in
`ipc_handlers.js`. No dead channels, no missing handlers.

### Phase C audit — Authentication ↔ Frontend

Confirmed by code read: `login.html` → `fetch /auth/login` → token in
`sessionStorage`. Every other page (dashboard, live, transactions, etc.)
checks `sessionStorage.dws_session.token` on load; missing/falsy token
redirects to `login.html`. `shell.js` handles logout via `fetch
/auth/logout`. No mock-auth fallback exists beyond the `admin/admin`
dev-catch in `login.html` (which requires the real API fetch to throw,
i.e., the backend is not running at all).

### Phase D audit — Frontend ↔ Backend endpoint map

| Page | Endpoints called | Status |
|---|---|---|
| login.html | `POST /auth/login` | ✓ Real |
| dashboard.html | `GET /transactions?limit=100`, `GET /health` | ✓ Real (alias + existing) |
| live.html | `GET /scan/latest`, `GET /transactions?limit=8`, `WS :8765` | ✓ / graceful-degrade |
| transactions.html | `GET /transactions` | ✓ Alias |
| diagnostics.html | `GET /health`, `GET /transactions?limit=50`, `GET /system/metrics` | ✓ / ✓ / degrade |
| logs.html | `GET /logs`, `GET /errors`, `GET /transactions?limit=1000` | ✓ / ✓ / ✓ |
| calibration.html | `GET /calibration/status`, `POST /calibration/*` | all degrade |
| settings.html | `GET /settings`, `POST /settings` | degrade |
| users.html | `GET /auth/users`, `POST /auth/users`, `DELETE /auth/users/*` | ✓ Real (auth routes) |
| about.html | `GET /health`, `GET /` | ✓ Real |

### Phase F audit — Reliability ↔ Frontend

`diagnostics.js` polls `GET /health` on `api_server.py` (:8000) for the
component health grid. The reliability API at `:8001` is not directly
wired to the frontend HMI — it is a separate support/monitoring surface.
This is intentional: the operator's real-time health view comes from the
existing `:8000/health` endpoint which `health_monitor.py` keeps current.
The reliability layer provides deeper historical and recovery data for
support tooling, not operator dashboards.

### Phase H — Startup sequence (verified correct)

```
Electron main.js
  → step 1: WindowManager (splash shown)
  → step 2: Tray
  → step 3: IPC bridge (ipc_handlers registered)
  → step 4-5: ProcessSupervisor.startAll() — backend + api + hmi spawned
  → step 6-7: waitForApi(http://localhost:8000/health, 30s)
  → step 8: health monitor starts
  → step 9: window navigates to http://localhost:8080 → index.html → login.html
```

### Phase I — Shutdown sequence (verified correct)

`before-quit` event: healthMonitor.stop() → supervisor.stopAll() (SIGTERM
each child, wait) → app.exit(). SIGTERM handler also calls gracefulShutdown().
No orphan-process risk.

### Open items for future phases

| Item | Notes |
|---|---|
| Heartbeat call sites | `vision_node.py`, `scale_node.py`, `barcode_node.py`, `orchestrator_node.py` do not yet call `heartbeat.write()`. Requires Sprint-2 in-process wiring per `RELIABILITY_IMPLEMENTATION_GUIDE.md §10` |
| Recovery request consumers | `reliability_recovery_requests` table written by `recovery_manager.py` has no in-process consumer yet in `run_dws.py`. Sprint-2 work. |
| `health_api.py` live run | Port `:8001` service not yet added to `deploy/install_service.sh` or `start_dws.sh`. Sprint-2. |
| `/system/metrics` proxy | **Resolved in Phase 7.** Implemented as a thin route delegating to `diagnostic_report.collect_system_resources()`. |
| Calibration endpoints (`/calibration/start\|compute\|save`, `/system/state`) | Still not implemented; frontend gracefully degrades. Requires the orchestrator-level calibration state machine described in HANDOFF_PHASE6.md §5.5 — genuinely out of scope for a compatibility-endpoint pass; doing this without that state machine would mean fabricating backend behavior, which Phase 7's recovery constraints explicitly forbid. |

---

## Phase 7 — Repository Recovery Pass (2026-06-29)

**Scope:** Five concrete compatibility endpoints, one hardening fix,
one DB migration, one dependency reconciliation, stale-file cleanup.
No architecture changes. Full detail in `CHANGELOG.md`'s Phase 7
entry; summarized here for the merge-report audience.

### Endpoints added to `api_server.py`

| Route | Backed by | Notes |
|---|---|---|
| `GET /system/version` | `VERSION` file + `git` CLI | Degrades to `available: false` git block on non-git deployments |
| `GET /system/metrics` | `diagnostic_report.collect_system_resources()` | No psutil — matches existing `/proc`-based convention |
| `GET /logs` | `auth_login_history` table | Read-only; matches `hmi/js/logs.js`'s pre-existing expected contract |
| `POST /pipeline/start` `/stop` | `system_state.pipeline_running` | Reporting flag only — orchestrator untouched |
| `POST /scale/tare` | n/a | Always `403`; route did not exist before (was a `404`) |

### Conflicts / collision check

No naming collisions found. `GET /metrics` (transaction averages) and
`GET /system` (station info) were confirmed pre-existing and semantically
distinct from the new `GET /system/metrics` and `GET /system/version`
before adding the latter — see CHANGELOG Phase 7 entry. `GET /errors`
(DB-table error log) and the new `GET /logs` (auth audit trail) are also
distinct sources, intentionally not merged.

### Files touched

```
dws_ws/requirements.txt
dws_ws/src/dws_ros2/api_server.py
dws_ws/src/dws_ros2/dws_ros2/database_node.py
dws_ws/src/dws_ros2/reliability/diagnostic_report.py
```

`start_dws.sh`, `paths.py`, and `authentication/` confirmed byte-identical
to the pre-Phase-7 checkout (diffed directly, not just "not mentioned in
the diff").


