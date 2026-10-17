# Architecture

Current architecture after Phase B. Sections marked _(planned)_ are not yet implemented.

## Overview

```
Browser (React SPA)
      |  HTTP/JSON
      v
FastAPI (backend/main.py)  ---- routes (backend/api/routes)
      |                                   |
      |  service layer                    |  Pydantic schemas (backend/schemas)
      +--> backend/services/*             |
              |                           v
              |                     SQLAlchemy models (backend/models)
              |                           |
              +----------------------> SQLite (repeatfix.db)
```

**Key rule:** prioritization logic lives only in `backend/services/prioritization/`.
The frontend displays scores and explanations returned by the API; it must never keep a
second copy of the scoring formula.

## Module responsibilities

| Module | Responsibility |
|---|---|
| `backend/core/config.py` | Typed app settings (reads `.env`, no secrets committed). |
| `backend/core/constants.py` | Status vocabulary (`ALLOWED_STATUSES`, `RESOLVED_STATUSES`). |
| `backend/core/time.py` | UTC helpers (`utcnow`, `to_naive_utc`, `serialize_utc`). |
| `backend/core/errors.py` | Generic error handling (no stack-trace leakage). |
| `backend/core/logging.py` | Central logging setup. |
| `backend/db/session.py` | SQLAlchemy engine + session factory + `Base`. |
| `backend/db/deps.py` | FastAPI `get_db` dependency (rollback-safe). |
| `backend/models/complaint.py` | ORM tables: `complaints`, `complaint_history`. |
| `backend/schemas/` | Request/response validation (Pydantic v2, `extra="forbid"`). |
| `backend/api/routes/complaints.py` | Complaint CRUD + history endpoints. |
| `backend/api/routes/meta.py` | Aggregate counts endpoint. |
| `backend/services/complaints/` | Business logic: list/filter/sort, create/update, status rules, history writes. |
| `backend/services/prioritization/` | Deterministic, explainable scoring engine. |
| `backend/services/evaluation/` _(planned)_ | Baselines + metrics. |
| `backend/services/recurrence/` _(planned)_ | Duplicate/recurrence candidate detection. |
| `backend/services/simulation/` _(planned)_ | Resource-constrained scheduling simulation. |
| `frontend/src/services/api.ts` | Single fetch wrapper for all API calls. |
| `frontend/src/filtersUrl.ts` | Filter state ↔ URL query ↔ list params (`PAGE_SIZE`). |
| `frontend/src/components/`, `src/pages/` | Presentational components and routed pages. |

## Data flow

1. User creates/imports a complaint → validated by Pydantic schema → stored as an ORM row.
   Raw imported values (when present) are preserved separately from cleaned fields.
2. Updates go through the complaints service: field-level diffs are written to
   `complaint_history`, and `last_updated_at` is refreshed. Entering a resolved status
   (`resolved`/`closed`) sets `resolved_at`; reopening (`open`/`in_progress`) clears it.
3. The prioritization service reads complaint attributes → returns a score, component
   breakdown, rationale list, uncertainty flags, and missing-field list.
4. The API returns those structured results; the UI renders them as a plain-English
   explanation.

Timestamps are stored as **naive UTC** in SQLite and serialized by the API with an explicit
`+00:00` suffix.

## Complaint status rules

- Allowed statuses: `open`, `in_progress`, `resolved`, `closed`
  (`backend/core/constants.py`).
- `resolved_at` is set when status moves into a resolved status and cleared when it moves back
  to an unresolved one.
- History rows are **append-only**; no history entry is ever mutated or deleted by the app.

## Database schema (current)

### `complaints`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | surrogate key |
| `complaint_id` | TEXT UNIQUE NOT NULL | external/import identifier |
| `title` | TEXT NOT NULL | |
| `description` | TEXT | |
| `category` | TEXT | |
| `latitude` / `longitude` | REAL | optional |
| `location_label` / `administrative_area` | TEXT | optional |
| `created_at` / `last_updated_at` | DATETIME | server defaults |
| `resolved_at` | DATETIME | nullable |
| `status` | TEXT default `open` | |
| `assigned_department` | TEXT | |
| `source` | TEXT | provenance |
| `synthetic` | BOOLEAN default false | **synthetic vs. real distinction** |
| `evidence_metadata` | TEXT | free-form |
| `severity_label` | TEXT | independently assessed, may be absent |
| `data_quality_flags` | TEXT | encoded warnings |
| `priority_score` | REAL | nullable — not computed/stored via the API yet |
| `priority_rationale` | TEXT | |
| `raw_imported_values` | TEXT | original unmodified values (JSON) |

### `complaint_history`
| Column | Type |
|---|---|
| `id` | INTEGER PK |
| `complaint_id` | FK → complaints.id (CASCADE) |
| `field_name` | TEXT |
| `old_value` / `new_value` | TEXT |
| `changed_at` | DATETIME |
| `changed_by` | TEXT |

## API structure (current)

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | service banner |
| GET | `/api/v1/health` | health check |
| GET | `/api/v1/complaints` | list with filters/sort/pagination; total in `X-Total-Count` |
| POST | `/api/v1/complaints` | create; HTTP 400 on duplicate `complaint_id` |
| GET | `/api/v1/complaints/{complaint_id}` | fetch one; HTTP 404 if absent |
| PATCH | `/api/v1/complaints/{complaint_id}` | partial update; writes history |
| GET | `/api/v1/complaints/{complaint_id}/history` | append-only change history |
| GET | `/api/v1/meta/complaints` | overall total, status counts, category counts |

List query parameters: `status`, `category`, `location` (matches `location_label` or
`administrative_area`), `q` (substring search over id/title/description), `created_from`,
`created_to`, `resolved` (bool), `synthetic` (bool), `sort_by`, `sort_dir`, `skip`, `limit`.

Errors: Pydantic validation → HTTP 422 (field errors under `detail`); duplicate ID → HTTP 400
with `detail`; missing complaint → HTTP 404. `CORS` exposes `X-Total-Count` so the browser can
read the total. Stack traces are not returned to clients in normal operation.

## Prioritization module interface

`backend/services/prioritization/rule_based.py`:

- `PrioritizationInputs` — plain dataclass of inputs.
- `RuleBasedPrioritizer().score(inputs) -> PrioritizationResult`
  containing `priority_score` (0–1), `components`, `rationale`,
  `uncertainty_flags`, `data_quality_warnings`, `missing_fields`.

The result is **deterministic**: identical inputs and configuration always produce an
identical score. See `docs/prioritization_method.md`.
