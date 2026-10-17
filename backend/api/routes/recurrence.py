"""Recurrence API routes."""


from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.db.deps import get_db
from backend.models.complaint import Complaint
from backend.schemas.recurrence import (
    RecurrenceAnalysisRequest,
    RecurrenceAnalysisResponse,
)
from backend.services.recurrence.service import RecurrenceAnalysisService
from backend.services.recurrence.types import ComplaintInput

router = APIRouter()


def _to_input(c: Complaint) -> ComplaintInput:
    return ComplaintInput(
        complaint_id=c.complaint_id,
        title=c.title or "",
        description=c.description,
        category=c.category,
        latitude=c.latitude,
        longitude=c.longitude,
        location_label=c.location_label,
        administrative_area=c.administrative_area,
        created_at=c.created_at,
        source=c.source,
        synthetic=c.synthetic,
        data_quality_flags=c.data_quality_flags,
        raw_imported_values=c.raw_imported_values,
    )


@router.post("/recurrence/analyze", response_model=RecurrenceAnalysisResponse)
def analyze_recurrence(
    req: RecurrenceAnalysisRequest,
    db: Session = Depends(get_db),
):
    """Analyze potential recurrence relationships for a complaint.

    - Never merges/deletes complaints.
    - Returns explainable relations; text similarity alone is insufficient.
    - Uses only existing fields (raw_imported_values, data_quality_flags, source, etc.).
    """
    complaint = db.query(Complaint).filter(Complaint.complaint_id == req.complaint_id).first()
    if not complaint:
        raise HTTPException(
            status_code=404,
            detail={"message": "Complaint not found.", "details": None},
        )

    # Get candidates (excluding self). Limit to max_candidates for explainability.
    max_cand = max(1, min(200, req.max_candidates))
    candidates = (
        db.query(Complaint)
        .filter(Complaint.complaint_id != req.complaint_id)
        .order_by(Complaint.created_at.desc(), Complaint.id.desc())
        .limit(max_cand)
        .all()
    )

    service = RecurrenceAnalysisService()
    res = service.analyze(_to_input(complaint), [_to_input(c) for c in candidates])
    return RecurrenceAnalysisResponse.from_result(res)
