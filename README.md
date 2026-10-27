# RepeatFix

Research-Oriented Civic Complaint Prioritization System.

RepeatFix is a decision-support tool that investigates whether recurrence-aware, risk-based
prioritization can improve how civic authorities select and schedule high-priority complaints
under constrained service capacity.

It is **not** an autonomous decision-maker. Final operational decisions remain with authorized
human personnel. Predictions and scores are decision *support*, not determinations of danger
or urgency.

---

## Research Question

> Does recurrence-aware prioritization improve the ordering or handling of recurring civic
> complaints compared with FIFO, count-based, and rule-based baselines under constrained
> service capacity?

This is an open research question. No conclusion is claimed. The system provides the
infrastructure to investigate it.

---

## Implementation Status

| Feature | Status |
|---|---|
| Complaint CRUD and audit history | ✅ Implemented and tested |
| CSV import with dry-run preview | ✅ Implemented and tested |
| Complaint priority scoring (rule-based, at creation/import) | ✅ Implemented and tested |
| Priority score display in UI | ✅ Implemented |
| Recurrence analysis API | ✅ Implemented and tested |
| Recurrence analysis UI (complaint detail page) | ✅ Implemented |
| Label-free evaluation harness (CLI) | ✅ Implemented, script-accessible only |
| Discrete-event simulation (4 policies, CLI) | ✅ Implemented, script-accessible only |
| Evaluation/simulation UI | ❌ Not implemented (CLI only) |
| Authentication / authorization | ❌ Not implemented |
| Real-world dataset | ❌ None bundled; all samples are synthetic |
| Validated accuracy / calibration claims | ❌ No ground-truth labels available |

---

## Technology Stack

| Layer | Tech |
|---|---|
| Frontend | React 19, TypeScript, Vite 8 |
| Backend | Python 3.11, FastAPI, Pydantic v2 |
| Database | SQLite via SQLAlchemy 2.x |
| Analysis / Eval | Custom harness; no external ML framework |
| Simulation | Custom discrete-event engine (no external library) |
| Tests | pytest (backend), Vitest (frontend) |

---

## Prerequisites

- Windows PowerShell
- Python **3.11** (`py -V:3.11`; older/newer versions may lack prebuilt scientific wheels)
- Node.js 18+ and npm (tested with Node 22 / npm 10)

---

## Setup

From the project root `D:\RepeatFix`:

```powershell
# 1. Backend virtual environment
py -V:3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Backend dependencies
pip install -r backend\requirements.txt

# 3. Frontend dependencies
Push-Location frontend
npm install
Pop-Location
```

---

## Running the Application

**Backend** (terminal 1, `.venv` active):

```powershell
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

**Frontend** (terminal 2):

```powershell
Push-Location frontend
npm run dev
Pop-Location
```

Open http://localhost:5173.

- API root: http://localhost:8000
- Interactive API docs: http://localhost:8000/docs
- Health check: http://localhost:8000/api/v1/health

---

## Environment Configuration

Copy `frontend\.env.example` to `frontend\.env` if you need to override the API base URL:

```
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

Backend settings are in `backend/core/config.py` (reads `.env` if present).
**No secrets are required for local development.** Do not commit credentials.

---

## Database

RepeatFix uses SQLite (file: `repeatfix.db` in the project root).
The database is created automatically on first startup — no migration tool is required.
The file is excluded from version control via `.gitignore`.

---

## API Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | Health check |
| GET | `/api/v1/complaints` | List with filters, sorting, pagination (`X-Total-Count` header) |
| POST | `/api/v1/complaints` | Create a complaint (score computed automatically) |
| GET | `/api/v1/complaints/{id}` | Fetch one complaint |
| PATCH | `/api/v1/complaints/{id}` | Partial update (records history) |
| GET | `/api/v1/complaints/{id}/history` | Append-only field-change history |
| POST | `/api/v1/recurrence/analyze` | Analyse recurrence for a complaint |
| GET | `/api/v1/meta/complaints` | Totals, status counts, category counts |
| GET | `/api/v1/imports/fields` | Supported import fields, aliases, limits |
| POST | `/api/v1/imports/csv` | Import CSV (`dry_run`, `mapping`, `source_label`, `synthetic`) |
| GET | `/api/v1/imports` | Import history (paginated) |
| GET | `/api/v1/imports/{import_id}` | Import detail / summary |

---

## Complaint Creation and Priority Scoring

Complaints can be created through the web UI (`/complaints/new`) or via the API:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/complaints `
  -ContentType 'application/json' `
  -Body '{
    "complaint_id": "DEV-1",
    "title": "Broken streetlight",
    "category": "Streetlights",
    "severity_label": "medium",
    "latitude": 51.5,
    "longitude": -0.1
  }'
```

**Priority scoring** is computed automatically on creation and CSV import using a transparent,
deterministic rule-based formula:

```
score = 0.40 × severity + 0.25 × age_norm + 0.20 × recurrence_evidence
      + 0.10 × evidence_completeness + 0.05 × location_completeness
      − uncertainty_penalty
```

- Missing `severity_label` is treated as **uncertainty**, not as low severity.
- Missing location sets `missing_location` in `uncertainty_flags`.
- The `priority_rationale` field stores a JSON breakdown of components, flags, and missing
  fields so the score is always explainable.
- The formula is a **transparent research baseline** — not a validated operational predictor.

See [`docs/prioritization_method.md`](docs/prioritization_method.md) for full details.

---

## CSV Import

RepeatFix supports CSV import with automatic column alias detection, explicit column mapping,
row-level validation, exact/potential duplicate detection, idempotency, provenance tracking,
and a dry-run preview mode. Priority scores are computed for each accepted row.

- UI: http://localhost:5173/imports
- Fields: `GET /api/v1/imports/fields`
- Import: `POST /api/v1/imports/csv` (multipart: `file`, optional `mapping` JSON, `source_label`, `synthetic`, `dry_run`)
- Limits: max 100 MB, max 50,000 rows; UTF-8 and UTF-8-BOM only

Sample file: `samples/demo_synthetic.csv` — **synthetic data only**, not real complaints.

See [`docs/data_import.md`](docs/data_import.md) for full details.

---

## Recurrence Analysis

The recurrence API identifies potentially recurring complaints using four signals:

| Signal | Threshold |
|---|---|
| Text similarity (TF-IDF-style) | ≥ 0.7 (conservative) |
| Location proximity | ≤ 200 m |
| Category compatibility | Same or broad-compatible |
| Time window | ≤ 30 days |

All four signals must be satisfied for a `potential_recurrence` relation to be reported.
**No automatic merge or action is ever taken.** Every result requires human review.
Results are labelled explicitly as not calibrated probabilities.

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/recurrence/analyze `
  -ContentType 'application/json' `
  -Body '{"complaint_id": "DEV-1", "max_candidates": 50}'
```

The complaint detail page in the UI includes an "Analyse recurrence" button that calls this
endpoint and displays matches inline.

See [`docs/recurrence_analysis.md`](docs/recurrence_analysis.md) for full details.

---

## Evaluation Baselines

A label-free evaluation harness compares three deterministic baselines on the loaded dataset:

- **FIFO** — first-in, first-out by arrival time
- **Count-based** — order by number of predecessor complaints in the same area
- **Rule-based** — order by `priority_score` (the formula above)

The harness applies a temporal as-of cutoff (no future data leakage), reports descriptive
counts, missing-data rates, and agreement statistics, and runs a signal ablation of the
recurrence service. Label-dependent metrics (precision, recall, F1) are explicitly reported
as `not_evaluated` because no ground-truth labels exist.

```powershell
.\.venv\Scripts\python.exe scripts\run_evaluation.py
.\.venv\Scripts\python.exe scripts\run_evaluation.py --cutoff 2026-06-01T00:00:00 --out data\eval_reports
```

Reports are written to `data/eval_reports/` (excluded from version control).

See [`docs/evaluation_plan.md`](docs/evaluation_plan.md) and
[`docs/research_evaluation_plan.md`](docs/research_evaluation_plan.md) for full details.

---

## Simulation Experiment

A controlled discrete-event simulation (Phase D3.3) compares four scheduling policies:

| Policy | Description |
|---|---|
| FIFO | First-in, first-out |
| Rule-based | Static rule-based priority score |
| Count-based | Predecessor complaint count |
| Recurrence-aware | Rule-based + bounded recurrence evidence cap |

The experiment uses an 18-cell factorial grid (3 load levels × 3 correlation regimes ×
2 detector visibilities) with common random numbers (CRN) for fair comparison.
M/M/1 validation confirms theoretical ρ ≈ estimated ρ.

A pilot run (R=2 replications) has been completed and verified bit-identical across
independent reruns. **R=2 is insufficient for statistical conclusions** — paired-contrast
intervals are explicitly marked `insufficient_replications` in the report.

```powershell
# Pilot run (R=2, fast)
.\.venv\Scripts\python.exe scripts\run_simulation.py --replications 2 --master-seed 20261021 --out data\sim_reports --stem pilot

# Production run (R=30, slow — request explicit approval first)
# .\.venv\Scripts\python.exe scripts\run_simulation.py --replications 30 --master-seed 20261021 --out data\sim_reports
```

Reports are written to `data/sim_reports/` (excluded from version control).

See [`docs/phase_d3_simulation_design.md`](docs/phase_d3_simulation_design.md) for the
full experimental design.

---

## Running the Tests

**Backend** (from project root, `.venv` active):

```powershell
# All backend tests
.\.venv\Scripts\python.exe -m pytest tests/backend/ -q

# Focused subsets
.\.venv\Scripts\python.exe -m pytest tests/backend/test_complaints.py tests/backend/test_imports.py -q
.\.venv\Scripts\python.exe -m pytest tests/backend/test_scoring_integration.py -v
.\.venv\Scripts\python.exe -m pytest tests/backend/test_recurrence.py -v
```

**Frontend** (from `frontend/`):

```powershell
Push-Location frontend

# Type-check
npm run typecheck

# Unit tests (Vitest)
npm test

# Production build
npm run build

Pop-Location
```

---

## Repository Structure

```
RepeatFix/
├── backend/
│   ├── api/routes/          # FastAPI route handlers
│   ├── core/                # Config, constants, time utilities
│   ├── db/                  # SQLAlchemy session and base
│   ├── models/              # ORM models (Complaint, ImportBatch, …)
│   ├── schemas/             # Pydantic request/response schemas
│   └── services/
│       ├── complaints/      # CRUD domain logic
│       ├── evaluation/      # Label-free evaluation harness
│       ├── imports/         # CSV ingestion and validation
│       ├── prioritization/  # Rule-based scorer
│       ├── recurrence/      # Multi-signal recurrence analysis
│       └── simulation/      # Discrete-event simulation (D3.3)
├── docs/                    # Architecture, plans, development log
├── frontend/
│   ├── src/
│   │   ├── components/      # Reusable React components
│   │   ├── pages/           # Page components (complaints, imports, …)
│   │   ├── services/api.ts  # Typed API client
│   │   └── types.ts         # Shared TypeScript types
│   └── tests/               # Vitest unit tests
├── samples/                 # demo_synthetic.csv (synthetic only)
├── scripts/                 # run_evaluation.py, run_simulation.py
└── tests/backend/           # pytest backend tests
```

---

## Documentation

| File | Contents |
|---|---|
| `docs/architecture.md` | Architecture, data flow, schema, API |
| `docs/data_dictionary.md` | Field definitions and provenance categories |
| `docs/data_import.md` | CSV import workflow, mapping, validation, duplicates |
| `docs/prioritization_method.md` | Scoring formula, assumptions, missing-value behavior |
| `docs/recurrence_analysis.md` | Recurrence signals, thresholds, explainability |
| `docs/evaluation_plan.md` | Research question, baselines, metrics, leakage prevention |
| `docs/research_evaluation_plan.md` | Full research evaluation design |
| `docs/phase_d3_simulation_design.md` | Simulation design, policies, grid, reproducibility |
| `docs/limitations.md` | Unsupported claims and data risks |
| `docs/development_log.md` | Change history, test results, remaining tasks |

---

## Known Limitations

- **No real dataset is bundled.** All bundled data is explicitly synthetic.
- **No ground-truth labels.** Label-dependent metrics (precision, recall, F1) cannot be
  computed and are not reported.
- **Priority scores are a research baseline** — not validated on real operational data.
  Scores are deterministic and explainable but not calibrated.
- **Recurrence detection is not calibrated.** Match confidence labels ("low", "medium",
  "high") are heuristic, not statistical probabilities.
- **Simulation results use R=2 replications** — insufficient for statistical conclusions.
  Paired-contrast intervals are explicitly flagged `insufficient_replications`.
- No authentication or authorization. The `changed_by` history field is a fixed placeholder.
- No delete endpoint; complaints are edited, not removed.
- SQLite is local/single-file; not intended for concurrent multi-user production.
- Evaluation and simulation are CLI-only; no UI for running them.
- No deployment has been performed; no deployment is claimed.

See [`docs/limitations.md`](docs/limitations.md) for the full list of unsupported claims.
