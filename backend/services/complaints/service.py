"""Complaint domain service.

Business rules live here (not in routes, not in the frontend):

- Create / update a complaint.
- Record an auditable history entry for every meaningful change.
- Status transitions manage ``resolved_at`` consistently.
- ``created_at`` is set at creation and never modified by ordinary updates.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend import models
from backend.core.constants import RESOLVED_STATUSES, SORTABLE_FIELDS
from backend.core.time import to_naive_utc, utcnow
from backend.schemas.complaint import ComplaintCreate, ComplaintUpdate

# User-editable fields (mirrors ComplaintUpdate). Provenance and system fields excluded.
PERMITTED_UPDATE_FIELDS: Sequence[str] = (
    "title",
    "description",
    "category",
    "latitude",
    "longitude",
    "location_label",
    "administrative_area",
    "status",
    "assigned_department",
    "severity_label",
    "data_quality_flags",
)

CHANGED_BY_API = "api"

# History rows for read-only fields are forbidden; guard rails for future changes.
READ_ONLY_FIELDS = frozenset(
    {
        "complaint_id",
        "created_at",
        "last_updated_at",
        "synthetic",
        "source",
        "evidence_metadata",
        "raw_imported_values",
        "priority_score",
        "priority_rationale",
        "id",
    }
)


class ComplaintNotFound(Exception):
    def __init__(self, complaint_id: str) -> None:
        self.complaint_id = complaint_id
        super().__init__(f"Complaint '{complaint_id}' not found")


class DuplicateComplaintId(Exception):
    def __init__(self, complaint_id: str) -> None:
        self.complaint_id = complaint_id
        super().__init__(f"Complaint '{complaint_id}' already exists")


def _fmt(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return to_naive_utc(value).isoformat()
    if isinstance(value, float):
        # Avoid float formatting noise (0.30000000000000004) in history.
        return repr(round(value, 10)) if value != int(value) else str(int(value))
    return str(value)


def record_history(
    db: Session,
    complaint: models.Complaint,
    field_name: str,
    old_value: Any,
    new_value: Any,
    changed_by: Optional[str] = CHANGED_BY_API,
) -> Optional[models.ComplaintHistory]:
    """Append one history row. Never updates or deletes prior history rows."""
    if field_name in READ_ONLY_FIELDS:
        raise ValueError(f"History cannot be written for read-only field '{field_name}'")
    if old_value == new_value:
        return None
    entry = models.ComplaintHistory(
        complaint_id=complaint.id,  # integer FK to complaints.id
        field_name=field_name,
        old_value=_fmt(old_value),
        new_value=_fmt(new_value),
        changed_at=utcnow(),
        changed_by=changed_by,
    )
    db.add(entry)
    return entry


def get_by_complaint_id(db: Session, complaint_id: str) -> models.Complaint:
    complaint = (
        db.query(models.Complaint)
        .filter(models.Complaint.complaint_id == complaint_id)
        .first()
    )
    if complaint is None:
        raise ComplaintNotFound(complaint_id)
    return complaint


def create_complaint(db: Session, payload: ComplaintCreate) -> models.Complaint:
    existing = (
        db.query(models.Complaint)
        .filter(models.Complaint.complaint_id == payload.complaint_id)
        .first()
    )
    if existing is not None:
        raise DuplicateComplaintId(payload.complaint_id)

    data = payload.model_dump()
    created_at = to_naive_utc(data.pop("created_at", None)) or utcnow()

    complaint = models.Complaint(**data, created_at=created_at, last_updated_at=utcnow())
    db.add(complaint)
    db.flush()
    record_history(
        db,
        complaint,
        field_name="status",
        old_value=None,
        new_value=complaint.status,
        changed_by="created",
    )
    db.commit()
    db.refresh(complaint)
    return complaint


def update_complaint(
    db: Session,
    complaint: models.Complaint,
    payload: ComplaintUpdate,
) -> models.Complaint:
    """Apply a partial update, recording history for each changed field.

    Status-transition rules:
    - entering ``resolved``/``closed`` sets ``resolved_at`` (if not already set)
      for the current resolution cycle;
    - leaving ``resolved``/``closed`` clears ``resolved_at`` (reopened);
    - history rows are appended in chronological order and never rewritten.
    """
    changes: List[Dict[str, Any]] = []
    provided: Dict[str, Any] = payload.model_dump(exclude_unset=True)

    for field in PERMITTED_UPDATE_FIELDS:
        if field not in provided:
            continue
        new_value = provided[field]
        if field == "status":
            _apply_status_rules(db, complaint, new_value, changes)
            continue
        old_value = getattr(complaint, field)
        if old_value == new_value:
            continue
        setattr(complaint, field, new_value)
        changes.append(
            {"field_name": field, "old_value": old_value, "new_value": new_value}
        )

    if changes:
        now = utcnow()
        complaint.last_updated_at = now
        for ch in changes:
            record_history(db, complaint, **ch)
        db.commit()
        db.refresh(complaint)
    return complaint


def _apply_status_rules(
    db: Session,
    complaint: models.Complaint,
    new_status: Optional[str],
    changes: List[Dict[str, Any]],
) -> None:
    if new_status is None:
        return
    old_status = complaint.status
    if old_status == new_status:
        return

    complaint.status = new_status
    changes.append(
        {"field_name": "status", "old_value": old_status, "new_value": new_status}
    )

    was_resolved = old_status in RESOLVED_STATUSES
    now_resolved = new_status in RESOLVED_STATUSES

    if now_resolved and not was_resolved and complaint.resolved_at is None:
        # New resolution cycle.
        resolved_at = utcnow()
        changes.append(
            {
                "field_name": "resolved_at",
                "old_value": complaint.resolved_at,
                "new_value": resolved_at,
            }
        )
        complaint.resolved_at = resolved_at
    elif not now_resolved and was_resolved:
        # Reopened: clear the resolution timestamp for this cycle.
        changes.append(
            {
                "field_name": "resolved_at",
                "old_value": complaint.resolved_at,
                "new_value": None,
            }
        )
        complaint.resolved_at = None
    # Same resolution state (resolved->closed, or open<->in_progress):
    # resolved_at is left untouched; no extra history row.


def get_history(db: Session, complaint: models.Complaint) -> List[Dict[str, Any]]:
    """Return history entries (chronological) for a complaint, joined with its
    external complaint_id for display."""
    rows = (
        db.query(models.ComplaintHistory)
        .filter(models.ComplaintHistory.complaint_id == complaint.id)
        .order_by(
            models.ComplaintHistory.changed_at.asc(), models.ComplaintHistory.id.asc()
        )
        .all()
    )
    return [
        {
            "id": h.id,
            "complaint_id": complaint.complaint_id,
            "field_name": h.field_name,
            "old_value": h.old_value,
            "new_value": h.new_value,
            "changed_at": h.changed_at,
            "changed_by": h.changed_by,
        }
        for h in rows
    ]


def _escape_like(term: str) -> str:
    """Escape LIKE wildcards so user input matches literally."""
    return (
        term.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _sort_column(sort_by: str):
    mapping = {
        "created_at": models.Complaint.created_at,
        "last_updated_at": models.Complaint.last_updated_at,
        "complaint_id": models.Complaint.complaint_id,
        "title": models.Complaint.title,
        "status": models.Complaint.status,
        "category": models.Complaint.category,
    }
    if sort_by not in SORTABLE_FIELDS:
        raise ValueError(f"Unsupported sort field '{sort_by}'")
    return mapping[sort_by]


def list_complaints(
    db: Session,
    *,
    status: Optional[str] = None,
    category: Optional[str] = None,
    location: Optional[str] = None,
    q: Optional[str] = None,
    created_from: Optional[datetime] = None,
    created_to: Optional[datetime] = None,
    resolved: Optional[bool] = None,
    synthetic: Optional[bool] = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    skip: int = 0,
    limit: int = 100,
) -> tuple[List[models.Complaint], int]:
    """Filter, sort, and paginate complaints inside the database.

    Returns (rows, total_matching). The total reflects the filtered set so the
    client can paginate correctly without loading everything into memory.
    """
    conditions = []
    if status:
        conditions.append(models.Complaint.status == status)
    if category:
        conditions.append(
            func.lower(models.Complaint.category) == category.strip().lower()
        )
    if location:
        pattern = f"%{_escape_like(location.strip())}%"
        conditions.append(
            or_(
                models.Complaint.location_label.ilike(pattern, escape="\\"),
                models.Complaint.administrative_area.ilike(pattern, escape="\\"),
            )
        )
    if q:
        term = q.strip()
        pattern = f"%{_escape_like(term)}%"
        conditions.append(
            or_(
                models.Complaint.title.ilike(pattern, escape="\\"),
                models.Complaint.description.ilike(pattern, escape="\\"),
                models.Complaint.complaint_id.ilike(pattern, escape="\\"),
                models.Complaint.location_label.ilike(pattern, escape="\\"),
            )
        )
    created_from_naive = to_naive_utc(created_from)
    created_to_naive = to_naive_utc(created_to)
    if created_from_naive is not None:
        conditions.append(models.Complaint.created_at >= created_from_naive)
    if created_to_naive is not None:
        conditions.append(models.Complaint.created_at <= created_to_naive)
    if resolved is not None:
        if resolved:
            conditions.append(models.Complaint.status.in_(RESOLVED_STATUSES))
        else:
            conditions.append(models.Complaint.status.notin_(RESOLVED_STATUSES))
    if synthetic is not None:
        conditions.append(models.Complaint.synthetic.is_(bool(synthetic)))

    total: int = (
        db.query(func.count(models.Complaint.id)).filter(*conditions).scalar() or 0
    )

    col = _sort_column(sort_by)
    direction = col.desc() if sort_dir == "desc" else col.asc()
    id_direction = (
        models.Complaint.id.desc() if sort_dir == "desc" else models.Complaint.id.asc()
    )
    rows: List[models.Complaint] = (
        db.query(models.Complaint)
        .filter(*conditions)
        .order_by(direction, id_direction)
        .offset(skip)
        .limit(limit)
        .all()
    )
    return rows, total
