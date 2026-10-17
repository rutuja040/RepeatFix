## 2026-10-17 — Phase C: CSV data ingestion and data quality

### Changes — backend
- Config: added `max_import_rows` (default 50000).
- Constants: added `IMPORT_STATUS_*` and `IMPORT_STATUSES`.
- Models: created `ImportBatch` and `ComplaintImportLink`; registered in `backend/models/__init__.py`; added `Complaint.import_links` relationship.
- Services: `backend/services/imports/` with `fields.py` (aliases/normalization), `mapping.py`, `validation.py` (row-level issues), `duplicates.py` (exact/signature + potential), `service.py` (bounded streaming read, two-phase transaction, identity/computation, persistence, error mapping).
- Schemas: `backend/schemas/imports.py` (`ImportIssue`, `ImportLimits`, `ImportFieldsResponse`, `ImportSummary`, `ImportListItem`); registered in `backend/schemas/__init__.py`.
- Routes: `backend/api/routes/imports.py` (`GET /imports/fields`, `POST /imports/csv`, `GET /imports`, `GET /imports/{import_id}`); registered in `backend/main.py`; version bumped to 0.3.0.
- Tests: added `tests/backend/test_imports.py` with comprehensive coverage (file handling, mapping, DQ, duplicates/idempotency, provenance/transactions/rollback, regressions) using synthetic inline fixtures; added `tests/backend/test_constants.py` (Phase B consistency).

### Changes — frontend
- Types: added import types (`ImportIssue`, `ImportLimits`, `ImportFieldInfo`, `ImportFieldsResponse`, `ImportSummary`, `ImportListItem`).
- API: added `getImportFields`, `importCsv` (multipart FormData), `listImports` (X-Total-Count), `getImport`; improved `errorMessage`/`importErrorDetails` for `{message,details}` envelope.
- Components: `ImportMappingEditor`, `ImportSummaryPanel`.
- Pages: `ImportsPage` with upload, mapping, dry-run, results, history (paginated); added route `/imports` and nav link "Import".
- Styles: added import-specific styles.
- Tests: added `tests/api_imports.test.ts`, `tests/imports_components.test.tsx`, `tests/imports_page.test.tsx`.

### Design decisions
- Two-phase transaction: commit batch (provisional failed), then persist accepted rows and update status in one transaction; on failure mark batch failed with accepted_rows=0.
- Dataset identity: `label:<source_label>` or `cols:<sha256(sorted normalized headers)[:16]>`.
- Idempotency via `source_key` (dataset_identity::source_record_id).
- Duplicates: exact rejected, potential flagged but never merged; within-file duplicates rejected.
- No uploads persisted to disk; bounded streaming with max size/rows. Issues capped at 500.
- UTF-8 and UTF-8-BOM only. CORS exposes `X-Total-Count`.

### Tests run
- `python -m pytest -q` → 112 passed
- `python -m ruff check .` → All checks passed
- `npm run typecheck` (frontend) → no errors
- `npm run test -- --run` (frontend) → 39 passed
- `npm run build` (frontend) → succeeded
- Live smoke: `GET /imports/fields` and `POST /imports/csv` (multipart) verified against uvicorn server; import accepted as expected.

### Remaining tasks
- (Phase C complete; next phases per roadmap)
- Phase D: FIFO + complaint-count baselines wired end-to-end, score explanations.
- Phase E: recurrence/duplicate analysis with uncertainty.
- Phase F: evaluation module (chronological splits, leakage checks, metrics).
- Phase G: resource-constrained simulation.
- Phase H: dashboard integration.
- Phase I: stabilization, full test/build pass, docs review.
