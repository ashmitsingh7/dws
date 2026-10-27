# HANDOFF_PHASE3.md — DWS Multi-Agent Integration (Agent 2 → Agent 3)

**Prepared by:** Agent 2 (this session)
**Date:** 2026-06-26
**Status at handoff:** Phases A, B, and 3 COMPLETE and COMMITTED. Repository is clean, 98/98 auth tests passing, git history intact.

Read this document in full before touching anything.

---

## 1. What Agent 3 Must Do

Your scope is **Phase 4 (Reliability — Agent 5)** and **Phase 5 (Frontend — Agent 4)**. Do NOT begin Phase 6 (final wiring) unless you complete both first.

Do NOT re-do Phases A, B, or 3. They are committed and verified.

---

## 2. Repository State

**Git log:**
```
<commit-hash> Phase 3: Integrate Agent 3 Authentication / RBAC
401827d       Docs: MERGE_REPORT.md, INTEGRATION_TEST_REPORT.md, HANDOFF_PHASE2.md
41ac221       Phase B: Integrate Electron desktop shell (Agent 2 deliverable)
2151013       Phase A: Integrate deployment tooling (Agent 1 deliverable)
2d9057c       Baseline: dws_finalv7 pre-integration (build/ and log/ stripped)
```

**Working tree is clean** — `git status` should show nothing uncommitted.

**Directory layout (additions since Phase B):**
```
dws_finalv7/
├── HANDOFF_PHASE3.md             ← this file
├── dws_ws/
│   ├── requirements.txt          ← now includes bcrypt>=4.1
│   └── src/dws_ros2/
│       ├── api_server.py         ← auth_router mounted, run_migration() on startup
│       ├── authentication/       ← 6 .py files (Agent 3)
│       │   ├── auth_api.py       ← has _process_ip_limiter singleton + _make_auth_manager()
│       │   ├── auth_manager.py
│       │   ├── password_manager.py
│       │   ├── permissions.py
│       │   ├── session_manager.py
│       │   └── user_manager.py
│       ├── database/             ← Agent 3 migration
│       │   ├── auth_migration.py
│       │   └── auth_schema.sql
│       ├── setup.py              ← now includes 'bcrypt' in install_requires
│       └── test/
│           ├── auth/             ← 7 test files, 98/98 passing
│           └── test_dws_system.py
```

---

## 3. Original Architecture (do not change without reading this)

Three OS processes — intentional:

| Process | Command | Ports | Role |
|---|---|---|---|
| `run_dws.py` | `ros2 run dws_ros2 run_dws` | WS :8765, MJPEG/captures :8090 | 9 in-process nodes via SimBus |
| `api_server.py` | `uvicorn api_server:app --port 8000` | :8000 | FastAPI REST, 27 routes + auth routes |
| HMI server | `python3 -m http.server 8080` | :8080 | Serves operator UI |

- `paths.py` is the single source of truth for all runtime paths.
- `start_dws.sh` has hardened venv-shadowing defence — do not modify it.
- Orchestrator deliberately never sends software tare — legal-metrology constraint. `/scale/tare` must NEVER be implemented; the UI control must be disabled in Phase 5.
- `sessions` table (shift/work-session stats) ≠ `auth_sessions` table (login sessions). These are DIFFERENT tables, no collision.

---

## 4. Phase A — Deployment (COMPLETE)

See MERGE_REPORT.md §2. Nothing remaining.

---

## 5. Phase B — Electron (COMPLETE)

See MERGE_REPORT.md §3. Nothing remaining.

**Important for Phase 5:** `window_manager.js` loads URL `http://localhost:8080` only. Zero Electron changes needed when HMI content is replaced.

---

## 6. Phase 3 — Authentication (COMPLETE)

See MERGE_REPORT.md §5 for full detail. Summary of what was done and what you need to know:

### What was integrated
- `authentication/` package (6 modules) at `dws_ws/src/dws_ros2/authentication/`
- `database/` (auth_migration.py + auth_schema.sql) at `dws_ws/src/dws_ros2/database/`
- `test/auth/` (7 test files, 98 tests)
- `api_server.py` patched: `app.include_router(auth_router)` + `@app.on_event("startup")` migration call

### Key modifications made to Agent 3's code

**1. `auth_api.py` — IP rate-limiter singleton:**
A `_process_ip_limiter` module-level singleton replaces per-request `AuthManager(conn)` construction. All route bodies use `_make_auth_manager(conn)` which assigns the shared limiter. This is the fix for the audit finding "AuthManager was being recreated on every request."

**2. `auth_api.py` — dynamic DB path resolution:**
`_get_conn()` resolves `DB_PATH` at call time (env var → `dws_ros2.paths` → fallback) rather than at module import time. This makes tests work correctly in the integrated repo where `dws_ros2` is importable.

**3. Test files — sys.path depth:**
`conftest.py` climbs 2 levels up from `test/auth/` (not 1) to reach `dws_ros2/`. `test_auth_api.py` uses `dirname(dirname(...))` at the appropriate depth. Other test files already used the 3-dirname form which happened to be correct.

### Test commands
```bash
# From dws_ws/src/dws_ros2/:
python3 -m pytest test/auth/ -q
# Expected: 98 passed, 1 warning (httpx deprecation — cosmetic)
```

---

## 7. Phase 4 — Reliability (Agent 5): YOUR FIRST TASK

**DO THIS FIRST, before Phase 5.**

### 7.1 Critical pre-check before touching anything

The original repo has `dws_ros2/dws_ros2/health_monitor.py` already. The Electron shell has its own `health_monitor.js` (renderer-side, different layer, different purpose — not a collision). Agent 5 delivers `health_manager.py`, `watchdog.py`, `recovery_manager.py`, `health_api.py`, `heartbeat.py`, `diagnostic_report.py`.

**These may overlap with `dws_ros2/health_monitor.py`.** You MUST read both before writing a single line of integration code. Determine:
- Is Agent 5 purely additive (new process/endpoints alongside the existing health_monitor)?
- Does Agent 5 replace or extend `health_monitor.py`?
- Are there class/function name collisions?
- Is there a port collision? (`health_api.py` is expected at `:8001` — verify this; `:8000` is `api_server.py`)

Document findings in MERGE_REPORT.md §5 before implementing.

### 7.2 Integration approach (follow regardless of what you find)

- Original repo is source of truth; minimal-diff; no invented APIs.
- If Agent 5 is purely additive, add as a 4th OS process.
- If Agent 5 extends `health_monitor.py`, adapt carefully — the existing node is load-bearing.
- Commit only after tests pass.

---

## 8. Phase 5 — Frontend (Agent 4): YOUR SECOND TASK

### 8.1 Agent 4 deliverable structure
- `agent4_frontend/public/` contains plain HTML/CSS/JS
- Pages: `login.html`, `dashboard.html`, `live.html`, `transactions.html`, `logs.html`, `calibration.html`, `settings.html`, `users.html`, `diagnostics.html`, `reports.html`, `about.html`
- JS modules in `public/js/`: `shell.js`, `ui_manager.js`, `page_router.js`, `navigation.js`, `dashboard.js`, `live.js`, `logs.js`, `calibration.js`, `settings.js`, `users.js`, `diagnostics.js`, `tables.js`, `chart_manager.js`, `modal_manager.js`, `toast_manager.js`, `loading_manager.js`, `theme_manager.js`, `form_components.js`, `component_library.js`, `about.js`

### 8.2 How to integrate

Point the HMI server at Agent 4's `public/` directory. The HMI server at `:8080` currently serves `dws_ws/src/dws_ros2/hmi/index.html`. Options:

1. Copy Agent 4's `public/` contents into `dws_ws/src/dws_ros2/hmi/` (replacing existing `index.html`)
2. Update `start_dws.sh`'s HMI server cwd to point at Agent 4's directory

Option 1 is simpler. Zero Electron changes needed regardless — `window_manager.js` loads `http://localhost:8080`.

### 8.3 Auth IPC gap — CONFIRM BEFORE ACTING

Agent 4's `login.html` must be read before concluding anything about auth. Tentative resolution: it uses direct `fetch('/auth/login')` over HTTP (bearer token, localStorage). If it uses `window.dws.invoke('auth:login', ...)` IPC calls instead, Electron work is needed. Confirm by reading `login.html` and the login JS.

### 8.4 Known endpoint gaps (do not invent backends)

| Frontend expects | Backend has | Resolution |
|---|---|---|
| `/transactions` | `/history` | Add thin alias route in `api_server.py` |
| `/system/state` | None | Leave graceful degradation; document |
| `/system/metrics` | None | Leave graceful degradation; document |
| `/calibration/status` | None | Leave graceful degradation; document |
| `/calibration/start` | None | Leave graceful degradation; document |
| `/calibration/compute` | None | Leave graceful degradation; document |
| `/calibration/save` | None | Leave graceful degradation; document |
| `/pipeline/{action}` | None | Leave graceful degradation; document |

The `/transactions` → `/history` alias is the ONLY one to implement.

### 8.5 Scale tare — NEVER implement

Agent 4's Live Scan page has a `/scale/tare` button. The orchestrator explicitly forbids software tare (legal-metrology constraint — `orchestrator_node.py` lines ~81-82 and ~2059). **Disable or remove the UI control; never implement the endpoint.**

---

## 9. Known Conflicts / Open Items

| # | Issue | Status for Agent 3 |
|---|---|---|
| 1 | Agent 4 frontend expects non-existent endpoints | Add `/transactions` alias only; document rest as graceful degradation |
| 2 | `/scale/tare` UI button | Disable/remove control in Agent 4's HTML/JS; never implement endpoint |
| 3 | Agent 3/2 auth IPC gap | Confirm by reading Agent 4's login code before acting |
| 4 | Agent 5 reliability vs pre-existing `health_monitor.py` | MUST audit before Phase 4 implementation |

---

## 10. Integration Philosophy (preserve this)

1. Original repository is always source of truth
2. Preserve every working feature already present
3. Adapt agent code to existing architecture — never the reverse
4. Prefer minimal-diff modifications
5. Never rewrite working systems
6. Never introduce placeholder implementations
7. Never fabricate backend APIs
8. If functionality doesn't exist, document it rather than invent it

---

## 11. Documentation Conventions

- **CHANGELOG.md:** Insert new entries immediately ABOVE the `<!-- INTEGRATION_LOG_INSERT_POINT -->` comment (line ~77), then re-add the marker. Match the existing prose level.
- **MERGE_REPORT.md:** Add §5 (Reliability) and §6 (Frontend). Current §5 is "Open Conflicts" — you may renumber or subsection as needed.
- **INTEGRATION_TEST_REPORT.md:** Add §4 (Reliability) and §5 (Frontend). Match existing detail level.
- **One git commit per completed, verified phase** — commit only when tests pass.

---

## 12. Environment Constraints

- No GUI/X server — Electron cannot be visually launched
- No ROS2 — `ros2 run` commands cannot execute
- No physical hardware — sensor checks report WARN/SKIP correctly
- No systemd — dry-run and config-check paths only
- Network allowlist: **excludes** `github.com`; **includes** `registry.npmjs.org`, `pypi.org`

---

## 13. Test Commands (re-run to verify Phase 3)

```bash
# From dws_ws/src/dws_ros2/:
python3 -m pytest test/auth/ -q
# Expected: 98 passed, 1 warning

# Deployment verify:
bash dws_ws/src/dws_ros2/deploy/verify_install.sh --no-hardware
# Expected: 15 PASS / 10 WARN / 4 FAIL (all FAILs: no venv/ROS2/colcon)

# Electron syntax:
for f in electron/*.js; do node --check "$f" && echo "PASS: $f"; done
# Expected: all PASS
```

---

## 14. Stop Condition for Agent 3

Your work is complete when:

✓ Phase 4 (Reliability) integrated, tested, committed
✓ Phase 5 (Frontend) integrated, `/transactions` alias added, tare disabled, committed
✓ All tests passing
✓ CHANGELOG.md, MERGE_REPORT.md, INTEGRATION_TEST_REPORT.md updated
✓ HANDOFF_PHASE4.md written
✓ Repository zipped and returned

Do NOT begin Phase 6 (final wiring / end-to-end testing) — that belongs to Agent 4.
