import json
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy.orm import Session

from backend import models, schemas
from backend.core.config import settings
from backend.db.deps import get_db
from backend.services.imports import service
from backend.services.imports.fields import SUPPORTED_FIELDS, REQUIRED_FIELDS

router = APIRouter()


def _fields_response() -> schemas.ImportFieldsResponse:
    return schemas.ImportFieldsResponse(
        fields=[
            schemas.ImportFieldInfo(
                name=spec.name,
                required=spec.required,
                max_length=spec.max_length,
                description=spec.description,
                aliases=list(spec.aliases),
            )
            for spec in SUPPORTED_FIELDS
        ],
        required_fields=list(REQUIRED_FIELDS),
        limits=schemas.ImportLimits(
            max_size_mb=settings.max_import_size_mb,
            max_rows=settings.max_import_rows,
            supported_types=list(settings.supported_import_types),
        ),
    )


@router.get("/imports/fields", response_model=schemas.ImportFieldsResponse)
def import_fields():
    """Supported target fields, aliases, and configured limits for the mapping UI."""
    return _fields_response()


def _parse_mapping(mapping: Optional[str]) -> Optional[dict]:
    if mapping is None or not mapping.strip():
        return None
    try:
        parsed = json.loads(mapping)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=400,
            detail={"message": "mapping must be valid JSON.", "details": None},
        )
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=400,
            detail={"message": "mapping must be a JSON object of target->column.", "details": None},
        )
    return parsed


@router.post("/imports/csv", response_model=schemas.ImportSummary)
def import_csv(
    file: UploadFile = File(..., description="CSV file to import"),
    mapping: Optional[str] = Form(default=None, description="JSON object mapping target->source column"),
    source_label: Optional[str] = Form(default=None, description="Stable dataset identity"),
    synthetic: bool = Form(default=False),
    dry_run: bool = Form(default=False),
    db: Session = Depends(get_db),
):
    """Import complaints from a CSV file (or preview with ``dry_run=true``)."""
    explicit = _parse_mapping(mapping)
    try:
        return service.run_import(
            db,
            upload_file=file.file,
            filename=file.filename,
            content_type=file.content_type,
            mapping_explicit=explicit,
            source_label=source_label,
            synthetic_default=synthetic,
            dry_run=dry_run,
        )
    except service.ImportError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"message": exc.message, "details": exc.details},
        )


def _to_list_item(batch: models.ImportBatch) -> schemas.ImportListItem:
    return schemas.ImportListItem(
        import_id=batch.import_id,
        status=batch.status,
        source_filename=batch.source_filename,
        source_label=batch.source_label,
        dataset_identity=batch.dataset_identity,
        created_at=batch.created_at,
        completed_at=batch.completed_at,
        total_rows=batch.total_rows,
        accepted_rows=batch.accepted_rows,
        rejected_rows=batch.rejected_rows,
        skipped_rows=batch.skipped_rows,
        flagged_rows=batch.flagged_rows,
        exact_duplicate_rows=batch.exact_duplicate_rows,
        potential_duplicate_rows=batch.potential_duplicate_rows,
        message=batch.message,
    )


@router.get("/imports", response_model=List[schemas.ImportListItem])
def list_imports(
    response: Response,
    db: Session = Depends(get_db),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
):
    rows, total = service.list_imports(db, skip=skip, limit=limit)
    response.headers["X-Total-Count"] = str(total)
    return [_to_list_item(b) for b in rows]


@router.get("/imports/{import_id}", response_model=schemas.ImportSummary)
def get_import(import_id: str, db: Session = Depends(get_db)):
    try:
        batch = service.get_import(db, import_id)
    except service.ImportNotFound:
        raise HTTPException(
            status_code=404, detail={"message": "Import not found.", "details": None}
        )
    mapping = json.loads(batch.mapping_json) if batch.mapping_json else {}
    unmatched = json.loads(batch.unmatched_json) if batch.unmatched_json else []
    issues = json.loads(batch.issues_json) if batch.issues_json else []
    return schemas.ImportSummary(
        status=batch.status,
        dry_run=batch.dry_run,
        total_rows=batch.total_rows,
        accepted_rows=batch.accepted_rows,
        rejected_rows=batch.rejected_rows,
        skipped_rows=batch.skipped_rows,
        flagged_rows=batch.flagged_rows,
        exact_duplicate_rows=batch.exact_duplicate_rows,
        potential_duplicate_rows=batch.potential_duplicate_rows,
        issues_truncated=batch.issues_truncated,
        import_id=batch.import_id,
        source_filename=batch.source_filename,
        source_label=batch.source_label,
        dataset_identity=batch.dataset_identity,
        content_sha256=batch.content_sha256,
        created_at=batch.created_at,
        mapping=mapping,
        detected_columns=[],
        unmatched_columns=unmatched,
        ambiguous={},
        missing_required=[],
        issues=[schemas.ImportIssue(**i) for i in issues],
        message=batch.message,
    )
