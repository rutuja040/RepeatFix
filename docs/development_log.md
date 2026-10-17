# Development log

Append-only record of changes, tests, results, failures, and remaining work.

---

## 2026-10-17 — Phase A: Project foundation

### Changes
- Initialized empty Git repository; added `.gitignore`.
- Scaffolded Python backend (FastAPI) with layered structure:
  `backend/core`, `backend/db`, `backend/models`, `backend/schemas`, `backend/api/routes`,
  `backend/services/*`, `backend/utils`.
- Added `backend/core/config.py` (pydantic-settings), `backend/core/logging.py`.
- Added SQLAlchemy session/`get_db` dependency (SQLite file `repeatfix.db`).
- Added `Complaint` + `ComplaintHistory` models with provenance-oriented fields
  (`synthetic`, `raw_imported_values`, `data_quality_flags`, `priority_rationale`).
- Added Pydantic schemas and CRUD routes:
  `GET /api/v1/health`, `GET /api/v1/complaints`, `POST /api/v1/complaints`
  (rejects duplicate `complaint_id` with HTTP 400).
- App startup uses FastAPI `lifespan` to create tables (no table creation at import time).
- Scaffolded frontend with Vite + React + TypeScript.
  - Renamed default Vite `main.ts` → `main.tsx`, updated `index.html` script src.
  - Added `src/services/api.ts` fetch wrapper with `ApiError` handling.
  - `App.tsx` performs a live backend health check and displays connectivity state.
- Created `backend/requirements.txt` and `pyproject.toml` (pytest config).
- Created `samples/README.md` + `samples/demo_synthetic.csv` (labeled synthetic).
- Added `backend/services/prioritization/rule_based.py` — transparent, deterministic
  baseline prioritizer with uncertainty/missing-field flags (unit-tested).
- Added `backend/services/evaluation/baselines.py` — baseline enumerations (FIFO, count,
  rule-based) ready for Phase F.
- Documentation: `README.md`, this log, and initial `docs/*.md`.

### Environment notes
- System Python is **3.15.0**, but `pandas` failed to build there (no prebuilt wheel,
  meson/vswhere issue). Created the virtualenv with **Python 3.11.9** instead
  (`py -V:3.11 -m venv .venv`). All pinned deps installed cleanly under 3.11.

### Tests run
```
python -m pytest          -> 13 passed
npm run typecheck         -> no errors
npm test                  -> 1 passed (vitest)
npm run build             -> succeeded (vite production build)
```

### Known failures
- None currently.

### Remaining tasks
- Phase B: full complaint CRUD/update/history endpoints, filters, frontend list/detail.
- Phase C: CSV import with column mapping + validation + summary.
- Phase D: FIFO + complaint-count baselines wired end-to-end, score explanations.
- Phase E: recurrence/duplicate analysis with uncertainty.
- Phase F: evaluation module (chronological splits, leakage checks, metrics).
- Phase G: resource-constrained simulation.
- Phase H: dashboard integration.
- Phase I: stabilization, full test/build pass, docs review.

---

## 2026-10-17 — Phase B: Complaint data & management

### Changes — backend
- Added shared modules: `backend/core/constants.py` (status vocabulary),
  `backend/core/time.py` (UTC helpers), `backend/core/errors.py` (generic 500 handler that
  never leaks stack traces).
- Rewrote `backend/schemas/complaint.py`: bounded/validated fields (lat ±90, lon ±180, length
  limits, blank rejection), `extra="forbid"`, explicit UTC serialization, plus
  `ComplaintHistoryItem`, `CategoryCount`, and `ComplaintMeta` schemas.
- Added `backend/services/complaints/service.py`: create/update/history logic, status
  transition rules (`resolved_at` set/cleared), filtered + sorted + paginated listing with
  LIKE-escaping, and explicit `ComplaintNotFound` / `DuplicateComplaintId` errors.
- Rewrote `backend/api/routes/complaints.py` with list (filters, sort, `skip`/`limit`,
  `X-Total-Count`), create, detail, patch, and history endpoints. Added
  `backend/api/routes/meta.py` (`GET /api/v1/meta/complaints`).
- `backend/db/deps.py` gained rollback-safe session handling; `backend/main.py` v0.2.0 adds
  the lifespan, error handlers, meta router, and CORS `expose_headers=["X-Total-Count"]`.
- Tests: rewrote `tests/conftest.py` for isolated per-test temp databases and a
  `client_no_raise` fixture; added `tests/backend/test_complaints.py`.

### Changes — frontend
- Installed `react-router-dom`; added `src/types.ts`, expanded `src/services/api.ts`
  (query building, `X-Total-Count` parsing, field-error extraction, CRUD + history + meta),
  `src/format.ts`, and `src/filtersUrl.ts` (filter ↔ URL ↔ list-param mapping).
- Added components (`Layout`, `ComplaintFilters`, `ComplaintTable`, `ComplaintForm`,
  `HistoryList`, `Alert`, `Spinner`, `EmptyState`, `Pagination`) and pages (`HomePage`,
  `ComplaintsPage`, `ComplaintDetailPage`, `NewComplaintPage`, `EditComplaintPage`).
- Wired `App.tsx` routes, replaced the Vite template stylesheet with an app stylesheet,
  and set the `index.html` title/description.
- Moved frontend tests to `frontend/tests/` so bare-dependency resolution works; added
  `api_complaints.test.ts`, `filtersUrl.test.ts`, and `components.test.tsx`.

### Design decisions
- Timestamps stored as naive UTC in SQLite; API serializes with a `+00:00` suffix.
- `POST /complaints` returns HTTP 200 (kept from Phase A) rather than 201.
- List returns a JSON array with the total in the `X-Total-Count` header.
- History `complaint_id` remains an integer FK to `complaints.id` (existing schema; no
  migration, so the dev database is preserved).
- `changed_by` is a fixed `api` placeholder until an auth model exists.

### Tests run
```
python -m pytest          -> 65 passed
npm run typecheck         -> no errors
npm test                  -> 24 passed (vitest)
npm run build             -> succeeded (vite production build)
```
Live smoke test (uvicorn + vite dev server): CORS allow-origin + `X-Total-Count` exposure
confirmed; create → patch (status→resolved) → history returned 3 append-only rows; meta
statuses returned `open,in_progress,resolved,closed`. The dev database
(`repeatfix.db`, rows `T1`, `LIVE-B1`) was restored after the smoke test.

### Known failures
- None currently.

### Remaining tasks
- Phase C: CSV import with column mapping + validation + summary.
- Phase D: FIFO + complaint-count baselines wired end-to-end, score explanations.
- Phase E: recurrence/duplicate analysis with uncertainty.
- Phase F: evaluation module (chronological splits, leakage checks, metrics).
- Phase G: resource-constrained simulation.
- Phase H: dashboard integration.
- Phase I: stabilization, full test/build pass, docs review.
