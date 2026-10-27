"""CSV import orchestration.

Policy summary (see docs/data_import.md for the full contract):

- Uploads are parsed from a bounded in-memory buffer and never persisted.
- Only UTF-8 (optionally with BOM) is accepted.
- Rows are validated individually; invalid rows are rejected and reported, they
  never abort the whole import.
- Accepted rows plus the provenance batch are written in a single transaction.
  If writing fails the batch is recorded as ``failed`` with 0 accepted rows, so a
  failed import is never mistaken for a successful one.
- Idempotency uses ``source_key = dataset_identity::source_record_id``. Repeated
  imports of the same dataset skip already-imported records.
- Potential duplicates are only flagged, never merged or deleted.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import uuid
from typing import Any, Dict, List, Mapping, Optional

from sqlalchemy.orm import Session

from backend import models
from backend.core.config import settings
from backend.core.constants import (
    IMPORT_STATUS_COMPLETED,
    IMPORT_STATUS_EMPTY,
    IMPORT_STATUS_FAILED,
)
from backend.core.time import utcnow
from backend.services.complaints.service import _score_complaint
from backend.services.imports import duplicates
from backend.services.imports.duplicates import Candidate, PotentialDuplicateIndex
from backend.services.imports.fields import normalize_header
from backend.services.imports.mapping import MappingResult, build_mapping
from backend.services.imports.validation import Issue, RowResult, validate_row

logger = logging.getLogger("repeatfix.imports")

MAX_STORED_ISSUES = 500
EXISTING_CANDIDATE_CAP = 5000
READ_CHUNK_BYTES = 1024 * 1024

BASE_CODES = {
    "unsupported_file_type": "unsupported_file_type",
    "unsupported_encoding": "unsupported_encoding",
    "malformed_csv": "malformed_csv",
    "inconsistent_columns": "inconsistent_columns",
    "duplicate_source_record_id": "duplicate_source_record_id",
    "exact_duplicate": "exact_duplicate",
    "complaint_id_conflict": "complaint_id_conflict",
    "potential_duplicate": "potential_duplicate",
    "skipped_existing": "skipped_existing",
}


class ImportError(Exception):
    """Base class for import failures that map to a client HTTP status."""

    status_code = 400

    def __init__(self, message: str, *, details: Optional[Any] = None) -> None:
        self.message = message
        self.details = details
        super().__init__(message)


class UnsupportedFileType(ImportError):
    status_code = 400


class UnsupportedEncoding(ImportError):
    status_code = 400


class MalformedCSV(ImportError):
    status_code = 400


class ImportTooLarge(ImportError):
    status_code = 413


class TooManyRows(ImportError):
    status_code = 422


class InvalidMapping(ImportError):
    status_code = 422


class ImportNotFound(ImportError):
    status_code = 404


def _issue_dict(issue: Issue) -> Dict[str, Any]:
    return issue.to_dict()


def _dataset_identity(source_label: Optional[str], headers: List[str]) -> str:
    if source_label and source_label.strip():
        return "label:" + source_label.strip()
    signature = ",".join(sorted(normalize_header(h) for h in headers))
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
    return "cols:" + digest


def _read_upload_bounded(upload_file, max_bytes: int) -> bytes:
    stream = upload_file
    try:
        stream.seek(0)
    except (AttributeError, OSError):  # pragma: no cover - defensive
        pass
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = stream.read(READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ImportTooLarge(
                f"File exceeds the {settings.max_import_size_mb} MB limit.",
                details={"max_size_mb": settings.max_import_size_mb},
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnsupportedEncoding("File is not valid UTF-8. Only UTF-8 is supported.")


def _is_supported_upload(filename: Optional[str], content_type: Optional[str]) -> bool:
    if filename and filename.lower().endswith(".csv"):
        return True
    if content_type and content_type.split(";")[0].strip() in settings.supported_import_types:
        return True
    return False


def _new_issue(
    row_number: int, field: Optional[str], code: str, message: str, status: str
) -> Issue:
    return Issue(row_number=row_number, field=field, code=code, message=message, status=status)


def run_import(
    db: Session,
    *,
    upload_file,
    filename: Optional[str],
    content_type: Optional[str],
    mapping_explicit: Optional[Mapping[str, str]] = None,
    source_label: Optional[str] = None,
    synthetic_default: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    if not _is_supported_upload(filename, content_type):
        raise UnsupportedFileType(
            "Unsupported file type. Please upload a .csv file.",
            details={"supported_types": settings.supported_import_types},
        )

    data = _read_upload_bounded(upload_file, settings.max_import_size_mb * 1024 * 1024)
    if not data:
        return _empty_summary(
            filename, source_label, [], None, dry_run, message="File is empty."
        )

    text = _decode(data)
    content_sha256 = hashlib.sha256(data).hexdigest()

    if not text.strip():
        return _empty_summary(
            filename, source_label, [], content_sha256, dry_run, message="File is empty."
        )

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return _empty_summary(
            filename, source_label, [], content_sha256, dry_run, message="File is empty."
        )

    header = [h.replace("\ufeff", "").strip() for h in header]
    mapping_result = build_mapping(header, mapping_explicit)
    dataset_identity = _dataset_identity(source_label, header)

    if not mapping_result.is_valid:
        raise InvalidMapping(
            "Column mapping is invalid.",
            details={
                "missing_required": mapping_result.missing_required,
                "ambiguous": mapping_result.ambiguous,
                "errors": mapping_result.errors,
                "detected_columns": mapping_result.detected_columns,
            },
        )

    # --- read data rows (bounded) ---
    raw_rows: List[Dict[str, str]] = []
    parse_issues: List[Issue] = []
    row_number = 1  # header is row 1
    for row in reader:
        row_number += 1
        if len(raw_rows) >= settings.max_import_rows:
            raise TooManyRows(
                f"File exceeds the {settings.max_import_rows} row limit.",
                details={"max_rows": settings.max_import_rows},
            )
        if not any(cell.strip() for cell in row):
            continue  # skip fully blank lines
        if len(row) != len(header):
            parse_issues.append(
                _new_issue(
                    row_number,
                    None,
                    BASE_CODES["inconsistent_columns"],
                    f"Row has {len(row)} columns but the header has {len(header)}.",
                    "rejected",
                )
            )
            continue
        raw_rows.append(dict(zip(header, row)))

    map_hint = mapping_result.mapping
    results: List[RowResult] = [
        validate_row(raw, map_hint, row_number=idx + 2, default_synthetic=synthetic_default)
        for idx, raw in enumerate(raw_rows)
    ]

    return _finalize(
        db,
        results=results,
        parse_issues=parse_issues,
        mapping_result=mapping_result,
        dataset_identity=dataset_identity,
        content_sha256=content_sha256,
        filename=filename,
        source_label=source_label,
        dry_run=dry_run,
    )


def _load_existing_keys(db: Session, dataset_identity: str) -> set[str]:
    rows = (
        db.query(models.ComplaintImportLink.source_key)
        .filter(
            models.ComplaintImportLink.dataset_identity == dataset_identity,
            models.ComplaintImportLink.source_key.isnot(None),
        )
        .all()
    )
    return {r[0] for r in rows}


def _load_existing_candidate_index(db: Session, categories: set[str]) -> PotentialDuplicateIndex:
    index = PotentialDuplicateIndex()
    query = db.query(models.Complaint)
    if categories:
        query = query.filter(models.Complaint.category.in_(categories))
    for c in query.order_by(models.Complaint.id.desc()).limit(EXISTING_CANDIDATE_CAP).all():
        index.add(
            Candidate(
                key=c.complaint_id,
                category=c.category,
                text=duplicates.normalize_text(c.title, c.description),
                latitude=c.latitude,
                longitude=c.longitude,
                created_at=c.created_at,
            )
        )
    return index


def _finalize(
    db: Session,
    *,
    results: List[RowResult],
    parse_issues: List[Issue],
    mapping_result: MappingResult,
    dataset_identity: str,
    content_sha256: Optional[str],
    filename: Optional[str],
    source_label: Optional[str],
    dry_run: bool,
) -> Dict[str, Any]:
    issues: List[Issue] = list(parse_issues)
    accepted_specs: List[Dict[str, Any]] = []
    counts = {
        "total_rows": 0,
        "accepted_rows": 0,
        "rejected_rows": 0,
        "skipped_rows": 0,
        "flagged_rows": 0,
        "exact_duplicate_rows": 0,
        "potential_duplicate_rows": 0,
    }
    counts["total_rows"] = len(results)
    counts["rejected_rows"] = len(parse_issues)

    valid_results = [r for r in results if r.accepted]
    proposed_ids = sorted({r.values["complaint_id"] for r in valid_results if "complaint_id" in r.values})
    existing_keys = _load_existing_keys(db, dataset_identity)
    existing_ids = {
        r[0]
        for r in db.query(models.Complaint.complaint_id)
        .filter(models.Complaint.complaint_id.in_(proposed_ids))
        .all()
    } if proposed_ids else set()
    categories = {r.values.get("category") for r in valid_results if r.values.get("category")}
    index = _load_existing_candidate_index(db, categories)

    exact_seen: Dict[tuple, int] = {}
    seen_keys_in_file: Dict[str, int] = {}

    for result in results:
        if not result.accepted:
            counts["rejected_rows"] += 1
            issues.extend(result.errors)
            continue

        record_id = result.values["complaint_id"]
        source_key = f"{dataset_identity}::{record_id}"

        if source_key in existing_keys:
            counts["skipped_rows"] += 1
            issues.append(
                _new_issue(
                    result.row_number,
                    "complaint_id",
                    BASE_CODES["skipped_existing"],
                    f"Source record '{record_id}' was already imported; skipped (idempotent).",
                    "skipped",
                )
            )
            continue

        if source_key in seen_keys_in_file:
            counts["rejected_rows"] += 1
            issues.append(
                _new_issue(
                    result.row_number,
                    "complaint_id",
                    BASE_CODES["duplicate_source_record_id"],
                    f"Source record '{record_id}' repeats row {seen_keys_in_file[source_key]}; rejected.",
                    "rejected",
                )
            )
            continue

        if record_id in existing_ids:
            counts["rejected_rows"] += 1
            issues.append(
                _new_issue(
                    result.row_number,
                    "complaint_id",
                    BASE_CODES["complaint_id_conflict"],
                    f"Complaint id '{record_id}' already exists from a different dataset; rejected.",
                    "rejected",
                )
            )
            continue

        signature = duplicates.exact_signature(result.values)
        if signature in exact_seen:
            counts["rejected_rows"] += 1
            counts["exact_duplicate_rows"] += 1
            issues.append(
                _new_issue(
                    result.row_number,
                    None,
                    BASE_CODES["exact_duplicate"],
                    f"Exact duplicate of row {exact_seen[signature]}; rejected.",
                    "rejected",
                )
            )
            continue

        candidate = Candidate(
            key=record_id,
            category=result.values.get("category"),
            text=duplicates.normalize_text(
                result.values.get("title"), result.values.get("description")
            ),
            latitude=result.values.get("latitude"),
            longitude=result.values.get("longitude"),
            created_at=result.values.get("created_at"),
        )
        matches = index.find(candidate)
        if matches:
            for match in matches:
                result.warnings.append(
                    _new_issue(
                        result.row_number,
                        None,
                        BASE_CODES["potential_duplicate"],
                        f"Possibly similar to '{match['key']}' ({', '.join(match['reasons'])}); "
                        "flagged for review only.",
                        "flagged",
                    )
                )

        exact_seen[signature] = result.row_number
        seen_keys_in_file[source_key] = result.row_number
        index.add(candidate)
        accepted_specs.append(
            {
                "result": result,
                "source_key": source_key,
                "record_id": record_id,
                "dataset_identity": dataset_identity,
            }
        )

    counts["accepted_rows"] = len(accepted_specs)
    for spec in accepted_specs:
        warnings = spec["result"].warnings
        if warnings:
            counts["flagged_rows"] += 1
            counts["potential_duplicate_rows"] += sum(
                1 for w in warnings if w.code == BASE_CODES["potential_duplicate"]
            )
            issues.extend(warnings)

    if counts["total_rows"] == 0 and not parse_issues:
        status = IMPORT_STATUS_EMPTY
    elif counts["accepted_rows"] == 0 and counts["skipped_rows"] == 0:
        status = IMPORT_STATUS_FAILED
    else:
        status = IMPORT_STATUS_COMPLETED

    stored_issues = issues[:MAX_STORED_ISSUES]
    issues_truncated = len(issues) > MAX_STORED_ISSUES

    summary: Dict[str, Any] = {
        "import_id": None,
        "status": status,
        "dry_run": dry_run,
        "source_filename": filename,
        "source_label": source_label,
        "dataset_identity": dataset_identity,
        "content_sha256": content_sha256,
        "created_at": utcnow(),
        **counts,
        "mapping": dict(mapping_result.mapping),
        "detected_columns": list(mapping_result.detected_columns),
        "unmatched_columns": list(mapping_result.unmatched_columns),
        "ambiguous": {k: list(v) for k, v in mapping_result.ambiguous.items()},
        "missing_required": list(mapping_result.missing_required),
        "issues": [_issue_dict(i) for i in stored_issues],
        "issues_truncated": issues_truncated,
        "message": None,
    }

    if dry_run:
        return summary

    import_id = uuid.uuid4().hex
    summary["import_id"] = import_id

    if status == IMPORT_STATUS_EMPTY:
        summary["message"] = "No data rows found."

    _persist_import(db, summary, accepted_specs, status, import_id)
    return summary


def _persist_import(
    db: Session,
    summary: Dict[str, Any],
    accepted_specs: List[Dict[str, Any]],
    status: str,
    import_id: str,
) -> None:
    batch = models.ImportBatch(
        import_id=import_id,
        source_filename=summary.get("source_filename"),
        source_label=summary.get("source_label"),
        dataset_identity=summary.get("dataset_identity"),
        content_sha256=summary.get("content_sha256"),
        status=IMPORT_STATUS_FAILED,  # provisional until rows commit
        dry_run=False,
        completed_at=None,
        total_rows=summary["total_rows"],
        accepted_rows=0,
        rejected_rows=summary["rejected_rows"],
        skipped_rows=summary["skipped_rows"],
        flagged_rows=summary["flagged_rows"],
        exact_duplicate_rows=summary["exact_duplicate_rows"],
        potential_duplicate_rows=summary["potential_duplicate_rows"],
        mapping_json=json.dumps(summary["mapping"]),
        unmatched_json=json.dumps(summary["unmatched_columns"]),
        issues_json=json.dumps(summary["issues"]),
        issues_truncated=summary["issues_truncated"],
        message=summary.get("message"),
    )
    db.add(batch)
    db.commit()  # phase 1: provenance is durable even if row writing fails

    try:
        _insert_accepted_rows(db, batch, accepted_specs, import_id, summary)
        batch.accepted_rows = len(accepted_specs)
        batch.status = status
        batch.completed_at = utcnow()
        batch.message = summary.get("message")
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Import %s failed while writing rows", import_id)
        _mark_failed(db, batch, summary)
        raise


def _insert_accepted_rows(
    db: Session,
    batch: models.ImportBatch,
    accepted_specs: List[Dict[str, Any]],
    import_id: str,
    summary: Dict[str, Any],
) -> None:
    for spec in accepted_specs:
        result: RowResult = spec["result"]
        values = result.values
        warning_codes = sorted({w.code for w in result.warnings})
        raw_payload = {
            "import_id": import_id,
            "source_label": summary.get("source_label"),
            "dataset_identity": summary.get("dataset_identity"),
            "source_row_number": result.row_number,
            "source_record_id": spec["record_id"],
            "source_values": result.raw,
        }
        complaint = models.Complaint(
            complaint_id=values["complaint_id"],
            title=values["title"],
            description=values.get("description"),
            category=values.get("category"),
            latitude=values.get("latitude"),
            longitude=values.get("longitude"),
            location_label=values.get("location_label"),
            administrative_area=values.get("administrative_area"),
            status=values.get("status", "open"),
            assigned_department=values.get("assigned_department"),
            source=values.get("source") or summary.get("source_label"),
            synthetic=values.get("synthetic", False),
            severity_label=values.get("severity_label"),
            evidence_metadata=values.get("evidence_metadata"),
            data_quality_flags=json.dumps(warning_codes) if warning_codes else None,
            raw_imported_values=json.dumps(raw_payload, ensure_ascii=False, default=str),
            created_at=values.get("created_at") or utcnow(),
            last_updated_at=utcnow(),
        )
        db.add(complaint)
        db.flush()

        # Score the accepted complaint with the production rule-based prioritizer.
        # Runs after flush so all fields (including complaint_id) are resolved.
        priority_score, priority_rationale = _score_complaint(complaint)
        complaint.priority_score = priority_score
        complaint.priority_rationale = priority_rationale

        db.add(
            models.ComplaintHistory(
                complaint_id=complaint.id,
                field_name="status",
                old_value=None,
                new_value=complaint.status,
                changed_at=utcnow(),
                changed_by="import",
            )
        )
        db.add(
            models.ComplaintImportLink(
                complaint_id=complaint.id,
                import_batch_id=batch.id,
                dataset_identity=spec["dataset_identity"],
                source_record_id=spec["record_id"],
                source_row_number=result.row_number,
                source_key=spec["source_key"],
            )
        )


def _mark_failed(db: Session, batch: models.ImportBatch, summary: Dict[str, Any]) -> None:
    try:
        batch.status = IMPORT_STATUS_FAILED
        batch.accepted_rows = 0
        batch.completed_at = utcnow()
        batch.message = "Import failed while writing records."
        db.add(batch)
        db.commit()
    except Exception:  # pragma: no cover - defensive
        db.rollback()
        logger.exception("Could not mark import %s as failed", batch.import_id)


def _empty_summary(
    filename: Optional[str],
    source_label: Optional[str],
    detected_columns: List[str],
    content_sha256: Optional[str],
    dry_run: bool,
    *,
    message: str,
) -> Dict[str, Any]:
    return {
        "import_id": None,
        "status": IMPORT_STATUS_EMPTY,
        "dry_run": dry_run,
        "source_filename": filename,
        "source_label": source_label,
        "dataset_identity": None,
        "content_sha256": content_sha256,
        "created_at": utcnow(),
        "total_rows": 0,
        "accepted_rows": 0,
        "rejected_rows": 0,
        "skipped_rows": 0,
        "flagged_rows": 0,
        "exact_duplicate_rows": 0,
        "potential_duplicate_rows": 0,
        "mapping": {},
        "detected_columns": detected_columns,
        "unmatched_columns": [],
        "ambiguous": {},
        "missing_required": [],
        "issues": [],
        "issues_truncated": False,
        "message": message,
    }


def list_imports(db: Session, *, skip: int, limit: int) -> tuple[List[models.ImportBatch], int]:
    from sqlalchemy import func

    total = db.query(func.count(models.ImportBatch.id)).scalar() or 0
    rows = (
        db.query(models.ImportBatch)
        .order_by(models.ImportBatch.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return rows, total


def get_import(db: Session, import_id: str) -> models.ImportBatch:
    batch = (
        db.query(models.ImportBatch)
        .filter(models.ImportBatch.import_id == import_id)
        .first()
    )
    if batch is None:
        raise ImportNotFound("Import not found.")
    return batch
