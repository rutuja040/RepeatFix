from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend import models, schemas
from backend.core.constants import ALLOWED_STATUSES
from backend.db.deps import get_db

router = APIRouter()


@router.get("/meta/complaints", response_model=schemas.ComplaintMeta)
def complaints_meta(db: Session = Depends(get_db)):
    """Reference data for filter controls. The backend is the source of truth
    so the frontend does not hardcode allowed statuses or category values."""
    total = db.query(func.count(models.Complaint.id)).scalar() or 0
    rows = (
        db.query(models.Complaint.category, func.count(models.Complaint.id))
        .filter(
            models.Complaint.category.isnot(None),
            models.Complaint.category != "",
        )
        .group_by(models.Complaint.category)
        .order_by(func.lower(models.Complaint.category).asc())
        .all()
    )
    return schemas.ComplaintMeta(
        total=total,
        statuses=list(ALLOWED_STATUSES),
        categories=[schemas.CategoryCount(category=c, count=n) for c, n in rows],
    )
