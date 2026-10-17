from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend import schemas
from backend.core.constants import ALLOWED_STATUSES, SORTABLE_FIELDS
from backend.db.deps import get_db
from backend.services.complaints import service

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/complaints", response_model=List[schemas.Complaint])
def list_complaints(
    response: Response,
    db: Session = Depends(get_db),
    status: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None, max_length=200),
    location: Optional[str] = Query(default=None, max_length=300),
    q: Optional[str] = Query(default=None, min_length=1, max_length=200),
    created_from: Optional[datetime] = Query(default=None),
    created_to: Optional[datetime] = Query(default=None),
    resolved: Optional[bool] = Query(default=None),
    synthetic: Optional[bool] = Query(default=None),
    sort_by: str = Query(default="created_at"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
):
    if status is not None and status not in ALLOWED_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{status}'. Allowed: {', '.join(ALLOWED_STATUSES)}",
        )
    if sort_by not in SORTABLE_FIELDS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid sort_by '{sort_by}'. Allowed: {', '.join(SORTABLE_FIELDS)}",
        )
    if created_from is not None and created_to is not None and created_from > created_to:
        raise HTTPException(
            status_code=422, detail="created_from must be earlier than created_to"
        )

    rows, total = service.list_complaints(
        db,
        status=status,
        category=category,
        location=location,
        q=q,
        created_from=created_from,
        created_to=created_to,
        resolved=resolved,
        synthetic=synthetic,
        sort_by=sort_by,
        sort_dir=sort_dir,
        skip=skip,
        limit=limit,
    )
    # Total of the filtered set; exposed via header (CORS-exposed in main.py).
    response.headers["X-Total-Count"] = str(total)
    return rows


@router.post("/complaints", response_model=schemas.Complaint)
def create_complaint(complaint: schemas.ComplaintCreate, db: Session = Depends(get_db)):
    try:
        return service.create_complaint(db, complaint)
    except service.DuplicateComplaintId:
        raise HTTPException(status_code=400, detail="Complaint ID already exists")
    except IntegrityError:
        # Fallback for a race between the check and insert.
        db.rollback()
        raise HTTPException(status_code=400, detail="Complaint ID already exists")


@router.get("/complaints/{complaint_id}", response_model=schemas.Complaint)
def get_complaint(complaint_id: str, db: Session = Depends(get_db)):
    try:
        return service.get_by_complaint_id(db, complaint_id)
    except service.ComplaintNotFound:
        raise HTTPException(status_code=404, detail="Complaint not found")


@router.patch("/complaints/{complaint_id}", response_model=schemas.Complaint)
def update_complaint(
    complaint_id: str,
    payload: schemas.ComplaintUpdate,
    db: Session = Depends(get_db),
):
    try:
        complaint = service.get_by_complaint_id(db, complaint_id)
    except service.ComplaintNotFound:
        raise HTTPException(status_code=404, detail="Complaint not found")
    return service.update_complaint(db, complaint, payload)


@router.get(
    "/complaints/{complaint_id}/history",
    response_model=List[schemas.ComplaintHistoryItem],
)
def get_complaint_history(complaint_id: str, db: Session = Depends(get_db)):
    try:
        complaint = service.get_by_complaint_id(db, complaint_id)
    except service.ComplaintNotFound:
        raise HTTPException(status_code=404, detail="Complaint not found")
    return service.get_history(db, complaint)
