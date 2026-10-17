# Data dictionary

Provenance categories kept strictly separate:

| Category | Meaning |
|---|---|
| **Raw imported** | Exactly as present in the source file; never modified in place. |
| **Cleaned** | Parsed/normalized values (e.g., ISO timestamps, valid coordinates). |
| **Derived** | Computed features (age, completeness, recurrence signals). |
| **Model prediction** | Output of the prioritization engine (score, flags). |
| **Human assessment** | Independently entered labels (e.g., `severity_label`, reviewer decisions). |
| **Evaluation label** | Ground truth used only for measuring (never a live input feature). |

## Complaint fields (current schema)

| Field | Type | Category | Required | Missing-value rule |
|---|---|---|---|---|
| `id` | INTEGER | system | auto | surrogate key |
| `complaint_id` | TEXT | raw (or system) | yes | must be unique; duplicate ⇒ HTTP 400 |
| `title` | TEXT | raw/cleaned | yes (min 1 char) | rejected if empty |
| `description` | TEXT | raw | no | allowed empty; counts toward completeness |
| `category` | TEXT | raw/cleaned | no | absent ⇒ completeness penalty |
| `latitude`, `longitude` | REAL | raw/cleaned | no | absent ⇒ `missing_location` flag; **not** a low score |
| `location_label` | TEXT | raw | no | |
| `administrative_area` | TEXT | raw | no | |
| `created_at` | DATETIME | cleaned | auto | server default `now` if not provided |
| `last_updated_at` | DATETIME | system | auto | set on update |
| `resolved_at` | DATETIME | cleaned | no | null until resolved |
| `status` | TEXT | cleaned | default `open` | |
| `assigned_department` | TEXT | raw | no | |
| `source` | TEXT | provenance | no | dataset/origin label |
| `synthetic` | BOOLEAN | provenance | default `false` | **true ⇒ synthetic, never real** |
| `evidence_metadata` | TEXT | raw | no | free-form source info |
| `severity_label` | TEXT | **human assessment** | no | absent ⇒ `missing_severity` uncertainty flag |
| `data_quality_flags` | TEXT | derived | no | encoded warnings |
| `priority_score` | REAL | **model prediction** | no | null until computed |
| `priority_rationale` | TEXT | **model prediction** | no | plain-English explanation |
| `raw_imported_values` | TEXT (JSON) | **raw** | no | original source values, unmodified |

## Complaint history fields

| Field | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | surrogate key |
| `complaint_id` | INTEGER FK → complaints.id | CASCADE delete (internal row id, not the external `complaint_id`) |
| `field_name` | TEXT | which field changed |
| `old_value` / `new_value` | TEXT | before/after (JSON-encoded when needed) |
| `changed_at` | DATETIME | server default (UTC) |
| `changed_by` | TEXT | actor; currently a fixed `api` placeholder (no auth system yet) |

## Validation rules (current)

- `extra="forbid"` on request models — unknown fields are rejected with HTTP 422.
- `latitude` ∈ [-90, 90], `longitude` ∈ [-180, 180].
- Text fields have maximum lengths (e.g. `title` 1–300 chars); blank strings are rejected
  where a value is required.
- On update, `complaint_id`, `created_at`, `synthetic`, `source`, `raw_imported_values`,
  `priority_score`, and `priority_rationale` are not user-editable.

## Distinguishing observed vs. predicted vs. user-entered

- `severity_label` and `assigned_department` are **user/human** inputs.
- `priority_score` / `priority_rationale` are **model outputs** (separate columns).
- `raw_imported_values` preserves **source** values; cleaned columns may differ from them.
- Missing `severity_label` is **missing evidence**, not "low severity".
