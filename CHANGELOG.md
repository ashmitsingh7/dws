# DWS System — Changelog

---

# DWS System — Changelog

---

## Integration Log — Multi-Agent Recovery Integration

This section tracks the integration of independently-built agent
deliverables into this repository. New entries are inserted
immediately above the `<!-- INTEGRATION_LOG_INSERT_POINT -->` marker,
in chronological order of when each phase was completed (oldest at the
top of this section, marker always last).

### Phase A — Deployment Tooling (Agent 1)

Added operator-facing recovery and maintenance scripts to
`dws_ws/src/dws_ros2/deploy/`, alongside the pre-existing `run.sh`,
`stop.sh`, and `install_service.sh`: `backup.sh`, `restore.sh`,
`update.sh`, `uninstall.sh`, `verify_install.sh`, `diagnostics.sh`, and
`diagnostic_report_example.txt`. Added `DEPLOYMENT_RECOVERY.md` at the
repo root and a corresponding "Operations & Recovery Scripts"
subsection in `README.md` §9.

One real bug was found and fixed: `restore.sh` referenced `$USER` when
building its systemd unit name but, unlike the other four new scripts,
did not guard it with `USER="${USER:-$(id -un)}"`. Under `set -u` this
crashed with `USER: unbound variable` in any shell where `USER` isn't
exported (e.g. some non-interactive or sudo contexts). Fixed by adding
the same guard already used elsewhere in this script set.

### Phase B — Electron Desktop Shell (Agent 2)

Added a desktop shell at `electron/` (sibling of `dws_ws/` and
`dws_runtime/`) that launches and supervises the three existing
backend processes (`run_dws.py`, `api_server.py`, the HMI static
server) and loads the operator UI from `http://localhost:8080` in a
native window. The backend architecture is unchanged — Electron is a
launcher and process supervisor, not a replacement for any existing
process.

Two real bugs were found and fixed during integration, not just
copied in as delivered:

- `process_supervisor.js` had no defense against the same
  venv-shadowing failure mode that `start_dws.sh` was previously
  hardened against — a stale `.venv` from a different checkout could
  silently be used instead of the correct one. Added
  `_verifyInterpreterIdentity()`, run once at the start of
  `startAll()`, which resolves `dws_ros2`'s import origin via
  `importlib.util.find_spec` and compares it against the expected
  workspace root before spawning `backend`/`api`. On mismatch, those
  two processes are not started, the existing `crashed`/`fatal` dialog
  path in `main.js` is used to surface the failure, and the `hmi`
  process (which has no `dws_ros2` import surface) is still allowed to
  start.
- The `api` process's working directory was wrong — it pointed one
  level too deep (`dws_ros2/dws_ros2/` instead of `dws_ros2/`), which
  would have broken `uvicorn api_server:app` on launch. Fixed to match
  `start_dws.sh`'s own `cd "$PKG_DIR"` behavior.

A duplicate, drifted `electron-builder.yml` was removed after
confirming (via an actual `electron-builder --dir` config-load run)
that `package.json`'s `build` field is the configuration actually in
effect; keeping the YAML around risked silent packaging breakage since
its `files` whitelist had fallen out of sync. Packaging scripts
(`build.sh`, `launch.sh`, `make_placeholder_icons.js`) were moved into
`electron/scripts/` to match the layout their own usage comments and
`package.json`'s `make-icons` entry already expected.

`window_manager.js` loads the URL `http://localhost:8080` only, never
a specific file, so no Electron-side change is required if the content
served at that port changes in a future phase.

### Phase 3 — Authentication / RBAC (Agent 3)

Added a complete authentication and role-based access control subsystem
to the production repository.

**Files added:**

- `dws_ws/src/dws_ros2/authentication/` — six-module Python package:
  `auth_manager.py` (login/logout/audit façade), `auth_api.py`
  (FastAPI `APIRouter` with `/auth/*` and `/admin/*` routes),
  `session_manager.py` (bearer-token session lifecycle),
  `user_manager.py` (account CRUD + lockout primitives),
  `password_manager.py` (bcrypt hashing + policy validation),
  `permissions.py` (RBAC constants and `has_permission()` helper).
- `dws_ws/src/dws_ros2/database/` — `auth_schema.sql` (idempotent DDL
  for `roles`, `permissions`, `role_permissions`, `auth_users`,
  `auth_sessions`, `auth_login_history`) and `auth_migration.py`
  (idempotent migration runner that applies the schema, seeds
  roles/permissions, and creates a bootstrap administrator on first run only).
- `dws_ws/src/dws_ros2/test/auth/` — seven test files (98 tests total):
  `conftest.py`, `test_auth_manager.py`, `test_auth_api.py`,
  `test_password_manager.py`, `test_permissions.py`,
  `test_session_manager.py`, `test_user_manager.py`.

**Files modified:**

- `dws_ws/src/dws_ros2/api_server.py` — added `auth_router` mount and
  a `@app.on_event("startup")` handler that calls `run_migration(DB_PATH)`
  once at process start. Both guarded by try/except so the server degrades
  gracefully if the auth package is absent. No existing route was changed.
- `dws_ws/requirements.txt` — added `bcrypt>=4.1`.
- `dws_ws/src/dws_ros2/setup.py` — added `'bcrypt'` to `install_requires`.

**Bugs found and fixed during integration:**

1. *IP rate-limiter reset on every request.* Each route constructed a
   fresh `AuthManager(conn)`, which reset the in-memory `_IpRateLimiter`
   counter on every login attempt. Fix: a `_process_ip_limiter` singleton
   is created at module import time; `_make_auth_manager(conn)` replaces
   all direct `AuthManager(conn)` calls and overwrites `am._ip_limiter`
   with the shared singleton.

2. *Test DB isolation broken when `dws_ros2.paths` is importable.*
   The test fixture monkeypatches `DWS_DB_PATH` and re-imports `auth_api`.
   In the integrated repo `dws_ros2` is on `sys.path`, so the module-level
   `from dws_ros2.paths import DB_PATH` ignored the env var. Fix:
   `_get_conn()` now resolves the path at call time — env var first, then
   `dws_ros2.paths`, then module-level fallback.

**Test results:** 98 passed, 0 failed, 1 warning (cosmetic httpx deprecation).

**Architecture preserved:** Existing `/session` route (shift statistics) is
untouched. `auth_sessions` table is named differently from the pre-existing
`sessions` table. No existing endpoint renamed or removed.

### Phase 4 — Reliability / Watchdog Layer (Agent 5)

Added a process/health/recovery supervision layer as a new, fully
additive `reliability/` package. No existing file in `dws_ws/src/dws_ros2/`
was modified — this layer is a second, independent process that watches
the existing system from the outside.

**Files added:**

- `dws_ws/src/dws_ros2/reliability/` — six-module Python package:
  `heartbeat.py` (heartbeat write/read primitive + event log, ~430
  lines), `health_manager.py` (state machine with hysteresis:
  STARTING → ONLINE → WARNING → ERROR → ONLINE, plus a hard-signal
  bypass straight to ERROR on process death, ~450 lines),
  `watchdog.py` (OS process liveness via PID files, CPU/RAM via
  `/proc`, DB reachability, heartbeat-freshness fan-out, ~550 lines),
  `recovery_manager.py` (backoff-gated recovery dispatch — OS-process
  restart lever via `deploy/run.sh`/`systemctl`, plus an in-process
  soft-recovery-request lever for backend-internal subsystems, ~560
  lines), `health_api.py` (read-only FastAPI surface: `/health`,
  `/health/full`, `/health/history`, `/diagnostics`, ~190 lines),
  `diagnostic_report.py` (CLI + library full point-in-time diagnostic
  snapshot, ~460 lines).
- `dws_ws/src/dws_ros2/test/reliability/` — five test files (28 tests
  total): `conftest.py`, `test_health_manager.py`, `test_heartbeat.py`,
  `test_watchdog.py`, `test_recovery_manager.py`.

**Files modified:** none outside the new `reliability/` directory.
`setup.py`, `package.xml`, `deploy/run.sh`, `dws_params.yaml`, and every
existing node/API file are untouched, per the architecture's own
"additive, Sprint-1-scoped" design (see
`reliability/RELIABILITY_ARCHITECTURE.md` §2 and §8). The layer's own
configuration is environment-variable-driven rather than added to
`dws_params.yaml`, deliberately, for the same reason.

**Bugs found and fixed during integration:**

1. *`mark_deliberately_stopped()` silently undone by the very next
   watchdog poll cycle.* `HealthManager.report_sample()`'s
   `deliberately_stopped` parameter defaulted to `False` and was
   unconditionally written into the component's stored state on every
   call — including `watchdog.py`'s own routine ~5s polls, which never
   pass that argument at all. The documented planned-maintenance
   workflow (operator calls `mark_deliberately_stopped(component,
   True)`, the component should report `OFFLINE` instead of `ERROR`
   until maintenance is over) was therefore non-functional: the flag
   reverted to `False` within one poll interval of being set. Fixed by
   changing the parameter's default to `None` and only writing it when
   the caller actually has an opinion this cycle — the same
   "`None` = no opinion" convention every other parameter on that
   method already follows. Confirmed no existing call site
   (`watchdog.py`'s three `report_sample()` call sites) depended on the
   old always-overwrite behavior, so this is a pure bug fix with no
   call-site changes required.

**Test results:** 28 passed, 0 failed (`health_manager`: 8,
`heartbeat`: 5, `watchdog`: 9, `recovery_manager`: 6). See
`INTEGRATION_TEST_REPORT.md` for the full run, including the manual
`watchdog.py --once` and `recovery_manager.py --dry-run` functional
smoke tests and the sandbox-environment caveats (no network access to
install `fastapi`/`pytest` in this environment — both are pre-existing,
already-declared dependencies, not new ones introduced by this phase).

**Architecture preserved:** `health_monitor.py`'s existing
`health_status` row, the existing `/health` endpoint in `api_server.py`,
`ScaleNode`'s serial reconnect logic, `VisionNodeRealSense`'s camera
startup retries, and `deploy/run.sh`'s PID-file conventions are all
read and cross-checked by the new layer, never replaced. The new
layer's own state lives in five new SQLite tables, all prefixed
`reliability_*`; no existing table's schema was touched.

### Phase 5 — Agent 4 Frontend HMI (2026-06-26)

**Integrated by:** Agent 2 (integration pass — Phase 5)

**What changed:** Agent 4's production HTML/CSS/JS operator interface
was copied verbatim into `dws_ws/src/dws_ros2/hmi/`, replacing the
single-page `index.html` with a full multi-page application: `login.html`,
`dashboard.html`, `live.html`, `transactions.html`, `reports.html`,
`diagnostics.html`, `logs.html`, `calibration.html`, `settings.html`,
`users.html`, `about.html`, plus supporting `css/` and `js/` directories
(17 CSS files, 20 JS files). `index.html` was replaced with a minimal
redirect to `login.html` so the Electron window manager's entry URL
(`http://localhost:8080`) correctly enters the authenticated flow.

**Compatibility alias:** `GET /transactions` added to `api_server.py` as
a thin alias for the existing `GET /history` handler. The alias is the
only backend change made; all other frontend endpoint expectations that
have no backend counterpart (`/system/state`, `/system/metrics`,
`/calibration/*`, `/pipeline/{action}`) are handled by the frontend's
own try/catch graceful-degradation paths — per the HANDOFF_2.md §8.3
resolution table.

**Scale tare permanently disabled:** The Live Scan page's "Tare Scale"
button (`btn-tare`, `live.html` and `live.js`) was disabled at both the
HTML layer (`disabled` attribute + `opacity:0.4; cursor:not-allowed`)
and the JS layer (click handler now emits a toast warning explaining the
legal-metrology constraint rather than calling `/scale/tare`). The
`/scale/tare` endpoint is not and must never be implemented — see
`orchestrator_node.py` lines ~81–82 and ~2059.

**Authentication:** `login.html` uses direct `fetch` calls to
`/auth/login` over HTTP and stores the bearer token in `sessionStorage`.
All other pages guard-check `sessionStorage.dws_session.token` on load
and redirect to `login.html` if absent. No Electron IPC (`auth:login`,
`AuthWindow`) changes were needed — the deferred TODO in the Phase 2
handoff is resolved by the frontend's HTTP-native auth approach.

**Calibration page wording:** The Calibration page title was updated to
"Calibration Verification" with a subtitle explicitly distinguishing the
operator accuracy-check workflow (known-parcel verification, every 100
shipments, operator-initiated) from system calibration
(`auto_calibration_v2.py`, runs at every boot, not operator-initiated).

**No existing files modified** except `api_server.py` (one new route)
and `hmi/index.html` (replaced with redirect). `start_dws.sh` and
Electron were not touched — the HMI server at `:8080` serves the `hmi/`
directory unchanged; Electron loads `http://localhost:8080` unchanged.

**Test results:** All Python syntax checks pass (13 files). All 20 HMI
JS files pass `node --check`. All 9 Electron JS files pass `node
--check`. All 9 deploy bash scripts pass `bash -n`. See
`INTEGRATION_TEST_REPORT.md §5` for full detail.

---

### Phase 6 — Final Integration Wiring (2026-06-26)

**Integrated by:** Agent 2 (integration pass — Phase 6)

**What changed:** System-level integration audit and wiring across all
five subsystems (Deployment, Electron, Authentication, Reliability,
Frontend). No new features were added; this phase verifies and cements
the cross-subsystem seams.

**Phase B (Electron ↔ Frontend):** `window_manager.js` loads
`http://localhost:8080` — zero path changes needed after Phase 5.
`preload.js`'s `window.dws` bridge exposes 14 IPC channels; all 14 have
matching `ipcMain.handle()` registrations in `ipc_handlers.js` — no
dead handlers, no missing handlers. Splash screen, tray, kiosk mode, and
reconnect logic are unaffected by the new multi-page frontend.

**Phase C (Authentication ↔ Frontend):** Confirmed: `login.html` calls
`/auth/login` via `fetch`, stores the bearer token in `sessionStorage`,
and all authenticated pages redirect to `login.html` on missing/invalid
token. `logout` calls `/auth/logout` with the Bearer header and clears
`sessionStorage`. No mock auth present.

**Phase D (Frontend ↔ Backend):** Every page's API calls were audited.
All calls to missing endpoints (`/system/metrics`, `/system/state`,
`/calibration/*`, `/pipeline/*`) already have try/catch graceful
degradation — gauges show "Unavailable", panels show empty/placeholder
states. The only implemented alias is `GET /transactions` → `GET
/history`, which maps real existing data rather than fabricating a new
feature.

**Phase F (Reliability ↔ Frontend):** `diagnostics.js` already polls
`GET /health` on `api_server.py` (:8000) for component health cards.
The reliability layer's `health_api.py` at `:8001` exposes `/health`,
`/health/full`, `/health/history`, and `/diagnostics` as a separate
read-only surface intended for support tools and future dashboards; no
frontend wiring to `:8001` was added this phase (the `:8000 /health`
endpoint already covers the operator's operational view).

**Phase H (Startup sequence):** `main.js` emits `startup:progress`
events (steps 1–9) covering window-manager init → tray → IPC bridge →
backend spawn → API readiness poll (30 s timeout) → HMI load. Order is
correct and unchanged.

**Phase I (Shutdown sequence):** `main.js` `before-quit` handler stops
health monitor, then calls `supervisor.stopAll()`. Process supervisor
sends SIGTERM to each child and waits. No orphan-process risk for the
three managed processes.

**Phase J (Calibration distinction):** `calibration.html` title updated
to "Calibration Verification"; subtitle now explicitly states that system
calibration runs at boot and is not operator-initiated.

**Phase K (Navigation audit):** All 10 nav targets (dashboard, live,
transactions, reports, diagnostics, logs, calibration, settings, users,
about) confirmed present as `.html` files on disk.

### Phase 7 — Repository Recovery: Compatibility Endpoints & Hardening (2026-06-29)

**Integrated by:** Recovery Agent (Phase 7)

**What changed:** Implemented the five concrete gaps Phase 6 left
open in §5.4/§5.5 of `HANDOFF_PHASE6.md` (missing backend endpoints),
plus stale-file and dependency cleanup. No architecture changes, no
redesign of the orchestrator or calibration state machine. Every
change below was smoke-tested against a real (migrated) SQLite DB via
`fastapi.testclient.TestClient`, not just syntax-checked.

**Stale file removal:** `start_dws.sh.bak` removed (was untracked,
`.gitignore`-covered `__pycache__/` dirs cleaned from the working
tree — both already correctly ignored, not a `.gitignore` defect).

**`api_server.py` — five new routes:**
- `GET /system/version` — application version, parsed `VERSION` file,
  and git commit hash/branch/timestamp (when the deployed checkout
  has a `.git` directory; degrades to `{"available": false}` on a
  packaged/non-git deployment rather than fabricating a hash).
- `GET /system/metrics` — real `cpu_percent`/`memory`/`disk` via
  `reliability/diagnostic_report.collect_system_resources()`. Reads
  `/proc/stat`, `/proc/meminfo`, and `shutil.disk_usage` directly —
  **no `psutil` dependency was added**, since `watchdog.py` and
  `diagnostic_report.py` already deliberately avoid it (see that
  module's own header comment). A new `cpu_percent()` helper was added
  to `diagnostic_report.py` using the same delta-sampling technique
  `watchdog.py` already uses per-process, applied to the aggregate
  `cpu` line in `/proc/stat` instead.
- `GET /logs` — thin read-only proxy over `auth_login_history` (the
  one append-only audit table this system already has — see
  `database/auth_schema.sql`). Supports `?limit=` and `?severity=`.
  Returns `{"logs": [], "count": 0}` if the table doesn't exist yet;
  never fabricates entries. Matches the response shape `hmi/js/logs.js`
  already expected (it was written against this endpoint's eventual
  existence — see its own header comment referencing a dedicated
  audit-log endpoint).
- `POST /pipeline/start` / `POST /pipeline/stop` — write-only flips of
  `system_state.pipeline_running`. Does not touch the orchestrator or
  `run_dws.py`; this is a reporting flag for the HMI only, exactly as
  scoped.
- `POST /scale/tare` — always returns `403` with "Software tare is
  intentionally disabled. Use the physical tare button on the
  weighing scale." No tare logic exists or was added anywhere in this
  codebase. This route previously did not exist at all (returned a
  plain `404`); it now exists and explicitly refuses, preserving the
  legal-metrology constraint documented throughout this project.

**`/calibration/latest` hardening:** wrapped the `calibration_history`
query in `try/except sqlite3.OperationalError`, so a fresh/uninitialized
database (calibration_history not yet migrated) returns
`{"available": false}` instead of a raw `500`. The table itself was
already created correctly by `database_node.py`'s startup migration —
this fix covers only the narrow window where the API is queried before
that migration has run.

**Database migration — `pipeline_running`:** Added to both the
`system_state` `CREATE TABLE IF NOT EXISTS` block (fresh databases)
and as an idempotent `ALTER TABLE ... ADD COLUMN` guarded by a
`PRAGMA table_info` check (existing databases), following the exact
pattern already used for `developer_mode` and the Build 005 settings
columns. Verified against both a synthetic fresh DB and a synthetic
pre-Phase-7 DB; both end up with the column, defaulting to `0`
(stopped).

**Dependency reconciliation:** `requirements.txt` was missing
`scikit-learn` (used by `perception_engine/segmentation/parcel_segmenter.py`),
`ultralytics` (used by `realsense_vision/human_detector.py`), and
`evdev` (used by `barcode_node.py` and `reliability/diagnostic_report.py`;
pinned with a `sys_platform == "linux"` marker since it's Linux-only).
All three are present in `setup.py`'s `install_requires` already — this
closes the gap in the other file. `requirements.txt`'s `scipy`,
`pillow`, `requests`, `python-dateutil`, `tqdm`, and `aiosqlite` have
no direct import sites found anywhere in this package; they were left
in place per the "do not remove dependencies without verifying" recovery
constraint, but are flagged here for a future pass to confirm whether
they're transitive needs of `opencv`/`open3d`/`torchvision` or genuinely
unused. `torch`/`torchvision` have no direct import sites either, but
are a real transitive dependency of `ultralytics` (YOLO), so they are
correctly retained. No dependencies were removed.

**Verification performed:** Full auth+reliability suite re-run after
all changes — 126/126 still passing (98 auth + 28 reliability, no
order-dependence). Full repo Python syntax sweep — all files pass.
20/20 HMI JS + 9/9 Electron JS + 9/9 deploy bash scripts re-verified
with `node --check` / `bash -n`. All five new/changed `api_server.py`
routes smoke-tested end-to-end against a real, migrated SQLite DB
(both the fresh-DB and existing-DB migration paths), not just import-
checked. `start_dws.sh`, `paths.py`, and the entire `authentication/`
package confirmed byte-identical to the pre-Phase-7 checkout.

<!-- INTEGRATION_LOG_INSERT_POINT -->