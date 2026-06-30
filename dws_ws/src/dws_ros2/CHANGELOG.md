# DWS System Changelog

## Build 005 — HMI Completion + Operations & Export (2026-06-22)

### Backend

**messages.py**
- Added `confidence: float = 1.0` to `TransactionMsg`

**orchestrator_node.py**
- `TransactionMsg` construction now threads in `confidence` from the perception pipeline
- `_raise_error` payload now includes `severity` (HIGH/MEDIUM derived from error type) and `component` (hardware/processing derived from error type prefix)

**database_node.py**
- `_handle_save`: INSERT now writes `confidence` column
- `_handle_error`: INSERT now writes `image_url`, `severity`, `component` columns
- `save_calibration_result`: INSERT now writes `operator` column
- New `get_errors_filtered(severity, component, limit)` — filtered error query
- New `backup_database()` — copies DB to BACKUP_DIR with timestamp

**hmi_bridge_node.py**
- `get_transactions`: result dicts now include `confidence`
- `get_errors`: result dicts now include `severity` and `component`

**api_server.py**
- Added `import io`, `import csv`, `import shutil`
- `_row_to_transaction`: now returns `latency_ms`, `operator`, `confidence`
- `GET /errors`: now accepts `severity` and `component` query-param filters; returns all new columns
- `GET /settings`: now returns all 6 settings fields (developer_mode, metrological_standard, strict_mode, allow_near_oos, station_name, operator_name)
- `POST /settings`: now accepts any combination of the 6 settings fields
- New `GET /export/csv`: streams transactions as CSV; optional `date_from`, `date_to`, `shift_id` filters
- New `POST /backup`: copies DB to BACKUP_DIR, returns filename
- New `GET /calibration/latest`: returns most recent calibration_history row

### HMI (index.html)

- **Bug fix**: `error_history` WebSocket handler was a no-op stub — now correctly merges persisted errors from DB into `errorLogs` on every connect/reconnect, deduplicating by id against any live events already received
- History table: added **Conf** column (green ≥80%, orange <80%)
- History pane header: added **⬇ CSV** and **💾 Backup DB** buttons
- Settings pane: added `id` attributes to all controls; added Station Name and Operator Name text inputs; added `settings-save-status` status line
- Settings **Save Settings** button now wired to `saveAllSettings()` which POSTs all 6 fields to `/settings`
- `loadSettingsPane()` now populates all 6 fields from `GET /settings`
- New `saveAllSettings()`, `exportCSV()`, `backupDB()` JS functions

## Build 005 INTERIM (schema only — 2026-06-22 earlier)

- `database_node.py`: schema + migrations for 5 new `system_state` columns, `confidence` on `transactions`, `image_url`/`severity`/`component` on `errors`, `operator` on `calibration_history`
- `paths.py`: added `BACKUP_DIR`, `EXPORT_DIR`

## Build 004 and earlier
See git history.
