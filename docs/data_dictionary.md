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

## Import batch fields

| Field | Type | Category | Notes |
|---|---|---|---|
| `id` | INTEGER | system | surrogate key |
| `import_id` | TEXT | system | UUID hex, unique |
| `source_filename` | TEXT | provenance | original filename |
| `source_label` | TEXT | provenance | dataset label if provided |
| `dataset_identity` | TEXT | provenance | `label:<label>` or `cols:<sha256[:16]>` |
| `content_sha256` | TEXT | provenance | SHA256 of uploaded content bytes |
| `mapping_json` | TEXT | provenance | target→source mapping used |
| `unmatched_json` | TEXT | provenance | unmatched source columns |
| `issues_json` | TEXT | provenance | per-row issues (truncated to 500) |
| `issues_truncated` | BOOLEAN | system | true if issues exceeded cap |
| `message` | TEXT | system | human-readable batch message |
| `status` | TEXT | system | `completed`, `empty`, `failed` |
| `dry_run` | BOOLEAN | system | true if no records persisted |
| `total_rows`, `accepted_rows`, `rejected_rows`, `skipped_rows`, `flagged_rows`, `exact_duplicate_rows`, `potential_duplicate_rows` | INTEGER | system | counts |
| `created_at`, `completed_at` | DATETIME | system | UTC (naive) |

## Complaint import link fields

| Field | Type | Category | Notes |
|---|---|---|---|
| `id` | INTEGER | system | surrogate key |
| `complaint_id` | INTEGER | system | FK → complaints.id (unique; one import source per complaint) |
| `import_batch_id` | INTEGER | system | FK → import_batches.id |
| `dataset_identity` | TEXT | provenance | identity of dataset |
| `source_record_id` | TEXT | provenance | complaint_id from source |
| `source_row_number` | INTEGER | provenance | 1-based row in CSV (header=1, data starts 2) |
| `source_key` | TEXT | provenance | `<dataset_identity>::<source_record_id>` (unique, nullable) |

## Distinguishing observed vs. predicted vs. user-entered

- `severity_label` and `assigned_department` are **user/human** inputs.
- `priority_score` / `priority_rationale` are **model outputs** (separate columns).
- `raw_imported_values` preserves **source** values; cleaned columns may differ from them.
- Missing `severity_label` is **missing evidence**, not "low severity".
