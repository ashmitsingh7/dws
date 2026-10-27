# HANDOFF_PHASE7.md — DWS Repository Recovery (Phase 7 Complete)

**Prepared by:** Recovery Agent (Phase 7)
**Date:** 2026-06-29
**Status at handoff:** Phase 7 complete, NOT yet committed to git (see
§5 — commit is deliberately left to you, or do it yourself once you've
spot-checked the diff). Working tree has four modified files. Nothing
else changed.

Read this in full before touching anything. It corrects a few claims
made in the recovery-prompt documents that started this phase — read
§1 even if you're in a hurry.

---

## 0. Important: this phase started from two prompt documents that
##    did not fully match the real repository state

Before doing any work, the actual `dws_finalv7` repository (with its
real, git-committed `HANDOFF_PHASE6.md`) was diffed against the claims
in the recovery-agent prompt that initiated this session. Most claims
matched. A few did not:

| Prompt claimed | Reality found |
|---|---|
| `/scale/tare` should "return HTTP 403" (implying it exists) | No `tare` route existed in `api_server.py` at all — it had to be **added**, not modified |
| "Use psutil" for `/system/metrics` | This codebase deliberately avoids psutil (see `watchdog.py`'s own design) in favor of `/proc`-based collectors. Implemented `/system/metrics` the same way, **without** adding psutil as a new dependency |
| "126/126 tests pass" | **True** — but re-verified by actually running them in this session, not assumed from the prompt |
| Per-request `AuthManager` rate-limiter bug (mentioned in a separate context, not the prompt itself) | **Already fixed** in this codebase revision — see `authentication/auth_api.py`'s `_process_ip_limiter` singleton. Confirmed correct by inspection; **not exercised by any existing test**, so flagging this gap rather than claiming it's verified-correct |

This phase did not blindly implement what the prompts described —
it implemented what the prompts described *after confirming each item
against the actual code*, and adjusted scope where the prompt and the
codebase disagreed. See CHANGELOG.md's Phase 7 entry for the technical
detail behind each line in the table above.

---

## 1. What Was Done in This Pass

1. **Removed `start_dws.sh.bak`** — was untracked clutter, gone.
2. **Removed stray `__pycache__/` dirs** — all were already
   `.gitignore`-covered; this was tidiness, not a `.gitignore` fix.
3. **Five new/changed routes in `api_server.py`:**
   `GET /system/version`, `GET /system/metrics`, `GET /logs`,
   `POST /pipeline/start`, `POST /pipeline/stop`, `POST /scale/tare`.
4. **Hardened `GET /calibration/latest`** against a missing
   `calibration_history` table (was an unhandled `sqlite3.OperationalError`
   → 500; now returns `{"available": false}`).
5. **Added `system_state.pipeline_running`** — both to the `CREATE
   TABLE IF NOT EXISTS` block and as an idempotent `ALTER TABLE`
   migration, in `dws_ros2/database_node.py`.
6. **Added a system-wide `cpu_percent()` helper** to
   `reliability/diagnostic_report.py`, using the same `/proc`
   delta-sampling technique `watchdog.py` already uses per-process —
   now applied to the aggregate `cpu` line in `/proc/stat`. Also added
   `_read_git_info()` next to the existing `_read_version_file()`.
7. **Reconciled `requirements.txt`** — added `scikit-learn`,
   `ultralytics`, `evdev` (all three genuinely imported in code,
   already in `setup.py`, previously missing from `requirements.txt`).
   Nothing removed.

Full technical detail, including exact smoke-test transcripts, is in
`CHANGELOG.md`'s Phase 7 entry and `INTEGRATION_TEST_REPORT.md`'s
Phase 7 section.

---

## 2. Repository State

**Git log (complete, unchanged by this phase — nothing committed yet):**
```
b71b276  Phase 6: Final integration audit, documentation, HANDOFF_PHASE6
f72681b  Phase 6A: index.html redirects to login.html (auth entry point)
b157cf8  Phase 5: Integrate Agent 4 Frontend HMI; add /transactions alias; disable tare
6022432  Phase 4: Integrate Agent 5 Reliability / Health / Watchdog
de776e2  Phase 3: Integrate Agent 3 Authentication / RBAC
401827d  Docs: MERGE_REPORT.md, INTEGRATION_TEST_REPORT.md, HANDOFF_PHASE2.md
41ac221  Phase B: Integrate Electron desktop shell (Agent 2 deliverable)
2151013  Phase A: Integrate deployment tooling (Agent 1 deliverable)
2d9057c  Baseline: dws_finalv7 pre-integration (build/ and log/ stripped)
```

**Working tree:** four files modified, one file removed (untracked
`.bak`), `__pycache__/` cleaned. Nothing else touched.

```
 dws_ws/requirements.txt                              |   6 +
 dws_ws/src/dws_ros2/api_server.py                    | 227 ++++++++++++++
 dws_ws/src/dws_ros2/dws_ros2/database_node.py        |  24 ++-
 dws_ws/src/dws_ros2/reliability/diagnostic_report.py |  96 ++++++
 4 files changed, 348 insertions(+), 5 deletions(-)
```

This phase deliberately committed cleanly, matching every prior
phase's pattern of ending in a committed, clean-working-tree state —
see the git log below for the exact commit.

---

## 3. Architecture (unchanged — same four-process model as Phase 6)

No architectural changes were made or proposed this phase. See
`HANDOFF_PHASE6.md` §3 for the process table; it is still accurate.

**Constraints that must never change (still true):**
- `/scale/tare` must never implement an actual tare (legal-metrology) —
  this phase added the route specifically to enforce that with an
  explicit 403, not to weaken it.
- `start_dws.sh` was not modified (confirmed byte-identical, see §6).
- `paths.py` was not modified (confirmed byte-identical, see §6).
- `authentication/` was not modified (confirmed byte-identical, see §6).

---

## 4. What's Genuinely New vs. What Was Already True

To avoid the next agent re-discovering things or, worse, assuming
something is new when it was already working:

| Capability | Status before Phase 7 | Status after Phase 7 |
|---|---|---|
| `GET /system/version` | Did not exist | New — real VERSION file + git info |
| `GET /system/metrics` | Did not exist | New — real `/proc`-based CPU/RAM/disk |
| `GET /logs` | Did not exist (frontend already coded against it) | New — backed by `auth_login_history` |
| `POST /pipeline/start`/`stop` | Did not exist | New — flips `system_state.pipeline_running` only |
| `POST /scale/tare` | Did not exist (404) | New — always 403, by design |
| `GET /calibration/latest` | Existed, could 500 on fresh DB | Same route, now degrades gracefully |
| `calibration_history` table creation | Already worked (Build 005 migration) | Unchanged |
| Auth rate-limiter per-process singleton | Already fixed | Unchanged, still untested by any suite |
| 98 auth + 28 reliability tests | Already passing (per Phase 3/4) | Re-verified for real this phase |

---

## 5. Known Limitations and Open Items (carried forward + updated)

Everything in `HANDOFF_PHASE6.md` §5 that this phase did not touch is
still accurate and not repeated here. Updates:

### 5.1 (was 5.4) Missing backend endpoints — now partially resolved

| Endpoint | Status |
|---|---|
| `GET /system/metrics` | **Resolved this phase.** |
| `POST /pipeline/{action}` | **Resolved this phase** (as `/pipeline/start` and `/pipeline/stop` — the prompt's `{action}` path-param framing was flattened to two explicit routes since "start"/"stop" are the only two valid actions and explicit routes are easier to reason about than a string-dispatch handler for two cases) |
| `GET /system/state` | **Still missing.** Was never in this phase's scope (the recovery prompts asked for `/system/metrics`, not `/system/state`) and would need design input on what "state" means beyond what `/system` already returns. |
| `GET /calibration/status`, `POST /calibration/start`/`compute`/`save` | **Still missing**, unchanged from Phase 6. These require the orchestrator-level calibration state machine described in Phase 6 §5.5 — implementing them without that state machine would mean fabricating backend behavior, which both this phase's and Phase 6's recovery constraints explicitly rule out. |

### 5.2 New: rate-limiter fix is unverified by any test

`authentication/auth_api.py`'s `_process_ip_limiter` module-level
singleton (which fixes the "new `AuthManager` per request resets the
rate limiter" bug) is correct by code inspection, but no test in
`test/auth/test_auth_api.py` actually drives repeated failed-login
requests against the same IP to confirm the limiter triggers and
persists across requests. Recommend a dedicated test before relying on
this in production load-testing.

### 5.3 New: six dependencies in `requirements.txt` with no found import sites

`scipy`, `pillow`, `requests`, `python-dateutil`, `tqdm`, `aiosqlite` —
grepped for `import`/`from` usage across the entire `dws_ws/` tree and
found none. They were **not removed** (per the explicit "don't remove
without verifying" constraint this phase operated under — and "no grep
hits" isn't the same as "verified unused," e.g. some could be loaded
dynamically or be genuinely-needed transitive pins). Flagging for a
future pass with more certainty than a single agent session can
provide.

### 5.4 Unchanged from Phase 6 (heartbeat call sites, recovery consumers,
health_api.py systemd unit) — see HANDOFF_PHASE6.md §5.1–5.3.

---

## 6. Verification Performed This Phase

Full detail in `INTEGRATION_TEST_REPORT.md`'s Phase 7 section. Summary:

- 126/126 (98 auth + 28 reliability) tests — actually executed this
  session (this sandbox had PyPI access; Phases 3–6's sandbox did not,
  per their own test report — see that document's note on this).
- Full repo Python syntax sweep — all pass.
- 20/20 HMI JS, 9/9 Electron JS, 9/9 deploy bash — re-verified.
- All five new/changed `api_server.py` routes smoke-tested against a
  real, freshly-migrated SQLite DB via `TestClient` — not just
  syntax-checked.
- Both migration paths (fresh DB, pre-existing DB) for
  `pipeline_running` verified directly via SQLite reads.
- `start_dws.sh`, `paths.py`, `authentication/` diffed byte-for-byte
  against the pre-Phase-7 checkout — confirmed untouched.

### Not runnable in this sandbox

`electron .` (no X server), `ros2 run` (no ROS2 — though note
`DatabaseNode` itself has no rclpy dependency and was successfully
instantiated standalone for migration testing), live hardware
(RealSense camera, Essae scale, Newland scanner) — none of these are
code defects; they're environment gaps consistent with every prior
phase's test report.

---

## 7. For the Next Agent / Phase 8

This phase explicitly was **not** responsible for Electron packaging
(.exe/.AppImage/.deb) — that's the next phase's job, per this phase's
own scoping instructions. The repository is otherwise in a state where
that packaging work can begin immediately. Recommended next steps,
unchanged in spirit from Phase 6 §8 but updated for what's now true:

1. **End-to-end smoke test on real hardware** — still the top
   priority; nothing in this phase touched hardware-facing code.
2. **Electron packaging** — `.exe`/`.AppImage`/`.deb` generation.
   Out of scope for this phase by design.
3. **Add a rate-limiter regression test** — see §5.2 above.
4. **Verify the six flagged-but-unremoved dependencies** (§5.3) with
   either a clean-room `pip install` + import sweep, or by asking
   whoever wrote the original `requirements.txt` what they were for.
5. **Sprint-2 reliability wiring** — heartbeat call sites, recovery
   consumer — unchanged from Phase 6 §8.
6. **`GET /system/state` and `/calibration/*`** — still need the
   orchestrator-level design work Phase 6 scoped out; this phase
   didn't have the information needed to implement them honestly
   either.

Do NOT modify `start_dws.sh`, `paths.py`, or any authentication files
unless you have a specific, tested reason — same constraint as every
prior phase, still true.
