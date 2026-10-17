# RepeatFix

Research-Oriented Civic Complaint Prioritization System.

RepeatFix is a decision-support tool that investigates whether recurrence-aware, risk-based
prioritization can improve how civic authorities select and schedule high-priority complaints.

It is **not** an autonomous decision-maker. Final operational decisions remain with authorized
human personnel. Predictions and scores are decision *support*, not determinations of danger
or urgency.

## Status

Phase C (CSV data ingestion and data quality) complete.

- Backend: CSV ingestion with bounded streaming, UTF-8 (with UTF-8-BOM handling), column alias
  detection and explicit mapping, row-level validation, duplicate/idempotency detection,
  provenance tracking, two-phase transaction, and persistent import metadata.
- Frontend: Import page with file upload, column mapping editor, dry-run preview, results,
  and import history; navigation link added.
- Tests: backend `pytest` (112 passing), frontend `vitest` (39 passing); lint/typecheck/build passing.
- Remaining features (recurrence analysis, evaluation, simulation, dashboard) are
  planned in later phases — see `docs/development_log.md`.

## Technology stack

| Layer | Tech |
|---|---|
| Frontend | React 19, TypeScript, Vite |
| Backend | Python 3.11, FastAPI, Pydantic v2 |
| Database | SQLite via SQLAlchemy 2.x |
| Analysis / Eval | pandas, NumPy, scikit-learn (used only where justified) |
| Tests | pytest (backend), Vitest (frontend) |

## Prerequisites

- Windows PowerShell
- Python **3.11** (created with `py -V:3.11`; Python 3.15 may not have prebuilt scientific
  wheels yet, so a 3.11 virtualenv is recommended)
- Node.js 18+ and npm (tested with Node 22 / npm 10)

## Setup

From the project root `D:\RepeatFix`:

```powershell
# 1. Backend: create and activate a virtual environment
py -V:3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install backend dependencies
pip install -r backend\requirements.txt

# 3. Frontend dependencies
Push-Location frontend
npm install
Pop-Location
```

## Running the application

Backend (PowerShell terminal 1):

```powershell
# from D:\RepeatFix, with .venv active
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend (PowerShell terminal 2):

```powershell
# from D:\RepeatFix
Push-Location frontend
npm run dev
Pop-Location
```

Then open http://localhost:5173 . The home page shows backend connectivity and complaint
counts; the complaint browser lives under `/complaints`.

- API root: http://localhost:8000
- API health: http://localhost:8000/api/v1/health
- Auto-generated API docs: http://localhost:8000/docs

### API endpoints (current)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | health check |
| GET | `/api/v1/complaints` | list with filters, sorting, pagination (`X-Total-Count` header) |
| POST | `/api/v1/complaints` | create a complaint |
| GET | `/api/v1/complaints/{complaint_id}` | fetch one complaint |
| PATCH | `/api/v1/complaints/{complaint_id}` | partial update (records history) |
| GET | `/api/v1/complaints/{complaint_id}/history` | append-only field-change history |
| GET | `/api/v1/meta/complaints` | totals, status counts, category counts |
| GET | `/api/v1/imports/fields` | supported fields, aliases, limits |
| POST | `/api/v1/imports/csv` | import CSV (supports `dry_run`, `mapping`, `source_label`, `synthetic`) |
| GET | `/api/v1/imports` | import history (paginated, `X-Total-Count`) |
| GET | `/api/v1/imports/{import_id}` | import detail/summary |

### Configuration

Copy `frontend\.env.example` to `frontend\.env` if you need to change the API base URL:

```
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

Backend settings live in `backend/core/config.py` (reads `.env` if present). Do not commit
secrets; this project currently requires none for local development.

## Running the tests

Backend:

```powershell
# from D:\RepeatFix, with .venv active
python -m pytest
```

Frontend (type-check + unit tests + production build):

```powershell
Push-Location frontend
npm run typecheck
npm test
npm run build
Pop-Location
```

## Importing a dataset

RepeatFix supports CSV import with automatic column alias detection, explicit overrides,
row-level validation, duplicate/idempotency controls, and a dry-run preview.

- UI: http://localhost:5173/imports (after starting frontend)
- Fields metadata: `GET /api/v1/imports/fields`
- Import: `POST /api/v1/imports/csv` (multipart: `file`, optional `mapping` JSON, `source_label`, `synthetic`, `dry_run`)
- Limits: max file size 100 MB, max rows 50,000; UTF-8 and UTF-8-BOM only; supported MIME `text/csv`, `application/csv`

Full details in [`docs/data_import.md`](docs/data_import.md).

You can also create complaints directly through the API:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/complaints `
  -ContentType 'application/json' `
  -Body '{"complaint_id":"DEV-1","title":"Streetlight out","synthetic":true,"category":"Streetlights"}'
```

Sample files in `samples/` are **synthetic demo data**, not real civic complaints.

## Documentation

- `docs/architecture.md` — architecture, data flow, schema, API structure
- `docs/data_dictionary.md` — field definitions and provenance categories
- `docs/data_import.md` — CSV import workflow, mapping, validation, duplicates, limits
- `docs/prioritization_method.md` — scoring formula, assumptions, missing-value behavior
- `docs/evaluation_plan.md` — research question, baselines, metrics, leakage prevention
- `docs/limitations.md` — unsupported claims and data risks
- `docs/development_log.md` — change history, test results, remaining tasks

## Known limitations (current)

- Prioritization is a transparent baseline only; it is **not** validated on real data yet,
  and scores are not computed/stored for complaints via the API yet.
- No recurrence analysis, evaluation, or simulation yet (planned).
- No authentication/authorization; history `changed_by` is a fixed placeholder (`api`).
- No delete endpoint (records are edited, not removed).
- No real dataset is bundled; all bundled rows are explicitly synthetic.
- No claims of real-world operational improvement are made or supported at this stage.
