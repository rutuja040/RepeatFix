# RepeatFix CSV Data Import (Phase C)

RepeatFix implements a CSV ingestion pipeline for importing civic complaints with explicit column mapping, row-level validation, duplicate detection, idempotency guarantees, and full provenance tracking. The design prioritizes data quality, auditability, and safety (no silent merges or deletions).

## Design goals

- **Safety first**: partial acceptance, explicit rejection reasons, no silent loss or merge of records.
- **Deterministic identity**: stable dataset identity to support idempotent re-imports.
- **Transparent provenance**: every imported complaint retains source identity and original values.
- **Bounded resource use**: streaming reads with size and row limits to prevent unbounded memory.
- **Defensive validation**: strict UTF-8, strict schema checks, and explicit issue reporting.

## Limits and configuration

- `max_import_size_mb` (default 100): maximum CSV file size.
- `max_import_rows` (default 50000): maximum number of data rows (excluding header).
- `supported_import_types`: `["text/csv", "application/csv"]`.
- `MAX_STORED_ISSUES` (500): cap on stored per-row issues per import batch.
- `EXISTING_CANDIDATE_CAP` (5000): cap for duplicate candidate scan (best-effort, not exhaustive at scale).
- `READ_CHUNK_BYTES` (1MB): bounded streaming chunk size.
- Duplicate detection thresholds: `SIMILARITY_THRESHOLD=0.85` (token Jaccard), `LOCATION_METERS=150.0`, `TIME_WINDOW_DAYS=7.0`.

All limits are surfaced via `GET /api/v1/imports/fields` in the `limits` object.

## Supported fields and mapping

The system supports target fields: `complaint_id` (required), `title` (required), `description`, `category`, `latitude`, `longitude`, `location_label`, `administrative_area`, `created_at`, `status`, `assigned_department`, `severity_label`, `data_quality_flags`, `synthetic`.

Each field has an ordered list of aliases (case-insensitive header matching). Header normalization: strip BOM, trim whitespace, collapse internal whitespace.

Mapping rules:
- Automatic detection via aliases; only auto-maps unambiguous matches (exact single alias hit).
- Explicit `mapping` override (JSON `target->source_column`) is honored.
- Ambiguous columns (multiple targets match the same source column) → 422 `ambiguous`.
- Missing required targets → 422 `missing_required`.
- Unknown target in explicit mapping → 422 `unsupported target`.
- Unmatched source columns are preserved in `raw_imported_values.source_values` and listed in `unmatched_columns`.

## Dataset identity and idempotency

`dataset_identity` is:
- `label:<source_label>` if `source_label` is provided and non-empty.
- `cols:<sha256(sorted normalized headers)[:16]>` otherwise.

`source_key` is computed as `"<dataset_identity>::<source_record_id>"`. `source_record_id` is the mapped `complaint_id` value for that row.

Idempotency:
- If `source_key` already exists (imported previously) → row marked `skipped` with code `skipped_existing`. No new complaint created.
- Re-importing the same file with the same identity produces `accepted_rows=0`, `skipped_rows = total_rows - rejected/other` (for rows already present).
- Complaint ID conflicts across different datasets (same `complaint_id` exists but from different `dataset_identity`) → row rejected `complaint_id_conflict`.

## Validation (row-level)

Validation produces `Issue` records (status `rejected`, `flagged`, or `skipped`). Issues are returned in the summary and persisted on the batch (truncated to 500).

- **Encoding**: UTF-8 and UTF-8-BOM only. Invalid UTF-8 → 400.
- **File type**: only `text/csv`/`application/csv` (by filename/content-type) → 400 otherwise.
- **Size/rows**: > max size → 413 `ImportTooLarge`; > max rows → 422 `TooManyRows`.
- **Malformed CSV**: inconsistent column counts → rejected `inconsistent_columns` per affected row.
- **Required fields**: `complaint_id`, `title` present and non-empty after trim → rejected `missing_required` if missing/blank.
- **Length**: values exceeding per-field `max_length` → rejected `value_too_long`.
- **Coordinates**: `latitude`/`longitude` parsed as numbers. Out of range (-90..90 / -180..180) → rejected `out_of_range`. Non-numeric → rejected `invalid_number`. One of lat/lon present but the other missing or empty → accepted but flagged `incomplete_coordinates`.
- **Timestamps**: `created_at` parsed as ISO 8601 (date, datetime, Z/± offset supported). Invalid → rejected `invalid_date`. Missing optional `created_at` → accepted but flagged `missing_created_at` and imported at import time (UTC).
- **Status**: if unknown or empty → defaults to `open` and flagged `unknown_status_defaulted`. Normalized to lowercase.
- **Category**: never coerced; kept verbatim (trimmed).
- **Synthetic flag**: mapped column `synthetic` → boolean.

## Duplicates

- **Within-file duplicates**: same `source_record_id` appears again → later row rejected `duplicate_source_record_id`.
- **Exact duplicate**: identical normalized signature (title, description, category, created_at day-granularity, lat/lon rounded to 4 decimals, administrative area) and different `complaint_id` from same dataset → rejected `exact_duplicate` (does not create a new complaint).
- **Potential duplicates**: similar text (`Jaccard >= 0.85`), same category (if present), same location within 150m (haversine), and created within 7 days → flagged `potential_duplicate` but **not rejected** and **not merged**. Both records are accepted.

## Transactions and persistence

Two-phase transaction model:
1. Insert `ImportBatch` with provisional status `failed` (and counts/metadata), commit.
2. Persist accepted rows (`_insert_accepted_rows`) and update batch status/counts (`completed` or `empty`) inside a single transaction.
3. On write failure, rollback, mark batch `failed` (with `accepted_rows=0`), raise 500. No partial commits of accepted complaints in case of mid-write failure.

Models:
- `ImportBatch`: metadata, counts, `mapping_json`, `unmatched_json`, `issues_json` (stored up to 500), `issues_truncated`, `message`, `status` (`completed`, `empty`, `failed`), `dry_run`, `dataset_identity`, `content_sha256`, `source_filename`, `source_label`, `created_at`, `completed_at`.
- `ComplaintImportLink`: provenance link from `complaint_id` (FK unique) to `import_batch_id`, with `dataset_identity`, `source_record_id`, `source_row_number`, `source_key` (unique, nullable). `Complaint.import_links` relationship exposes these links.

Provenance stored on `Complaint`:
- `source`: dataset identity (or fallback to "import").
- `raw_imported_values`: JSON containing `import_id`, `source_label`, `dataset_identity`, `source_record_id`, `source_row_number`, `source_key`, `source_filename`, `source_values` (original per-column values, including unmatched columns).
- `data_quality_flags`: aggregated flags from row issues (when present).

Uploads are read from `UploadFile.file` (stream) but are not persisted to disk; only in-memory streaming is used for reading/bounding.

## API

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/imports/fields` | Supported fields, aliases, required list, limits. |
| POST | `/api/v1/imports/csv` | Import CSV. Form fields: `file` (multipart), `mapping` (JSON string, optional), `source_label` (string, optional), `synthetic` (bool, default false), `dry_run` (bool, default false). Returns `ImportSummary`. |
| GET | `/api/v1/imports` | List import batches (paginated). Query: `skip`, `limit` (1..200). Returns array + `X-Total-Count` header. |
| GET | `/api/v1/imports/{import_id}` | Get import batch detail. Returns `ImportSummary`. 404 if not found. |

Status codes:
- 200 OK: success (including `empty` datasets)
- 400: malformed/empty/encoding/unsupported file type/JSON parse error
- 413: file too large
- 422: row count limit, invalid mapping (ambiguous/missing required/unsupported target), or validation policy violations surfaced as details
- 404: import not found
- 500: internal error (batch marked `failed`, no false success)

Error envelope: `{"message": string, "details": object | null}`.

## Output contracts

`ImportSummary` fields: `status`, `dry_run`, `total_rows`, `accepted_rows`, `rejected_rows`, `skipped_rows`, `flagged_rows`, `exact_duplicate_rows`, `potential_duplicate_rows`, `issues_truncated`, `import_id` (null for dry-run), `source_filename`, `source_label`, `dataset_identity`, `content_sha256`, `created_at`, `mapping` (target→source), `detected_columns`, `unmatched_columns`, `ambiguous`, `missing_required`, `issues[]`, `message`.

Note: `detected_columns`, `ambiguous`, `missing_required` are populated on the POST response (from the current import run). `GET /api/v1/imports/{import_id}` does not persist these fields; they may be empty there (by design — the stored batch focuses on outcomes).

## Frontend UI

The Imports page (`/imports`) provides:
- File upload with accept `.csv`/MIME hints and limits display.
- Source label input and synthetic flag toggle.
- **Preview (dry run)** and **Import** actions (double-submit guarded).
- Column mapping editor showing target fields (required/optional), detected columns, and aliases; supports explicit overrides; can reset to detected mapping.
- Preview/result panels showing counts, dataset identity/hash prefix, mapping used, unmatched columns, and row issues table (row, field, code, message, status with color-coded badges).
- Import history list (paginated by PAGE_SIZE=10) with "View" to load a past import detail into the result panel.

## Testing and verification

Backend tests cover: file handling (empty/whitespace/invalid encoding/unsupported type/size/row limits), mapping (auto/explicit/ambiguous/missing/unsupported/unmatched preservation), data quality (dates/tz, missing timestamps, coords ranges, blanks, long values, unknown status/category), duplicates/idempotency (within-file, exact, potential, cross-dataset conflict, re-import), provenance/transactions/rollback, and regressions (existing complaint CRUD). Synthetic CSV fixtures are inline.

Verification commands (Phase C):
- `.\.venv\Scripts\python.exe -m pytest -q` (112 passing)
- `.\.venv\Scripts\python.exe -m ruff check .` (clean)
- `cd frontend; npm run typecheck` (clean)
- `npm run test -- --run` (39 passing)
- `npm run build` (succeeds)
- Live smoke: fields + multipart import verified against uvicorn server.

## Known notes and limitations

- `GET /api/v1/imports/{import_id}` omits `detected_columns`, `ambiguous`, `missing_required` (not stored on batch). This is intentional to avoid duplicating transient mapping context; the POST summary is the authoritative preview/result for that run.
- Idempotency is based on `source_key` (dataset identity + source record id). Without `source_label`, dataset identity is derived from header signature — this is stable for identical column sets but may differ if column order/names change.
- Potential duplicate detection is best-effort (candidate cap 5000) and never merges records; flagged only.
- Exact duplicate is based on content signature, rejecting the second row when content is identical but ids differ.
- All timestamps are naive UTC internally; API serializes with `+00:00`.
- No real datasets are included; all example rows are synthetic.
- CSV import does not alter existing complaint schemas or delete data; only adds new tables/rows and provenance.
