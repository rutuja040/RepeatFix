from typing import Literal

ALLOWED_STATUSES: tuple[str, ...] = ("open", "in_progress", "resolved", "closed")
RESOLVED_STATUSES: tuple[str, ...] = ("resolved", "closed")

ComplaintStatus = Literal["open", "in_progress", "resolved", "closed"]

SORTABLE_FIELDS: tuple[str, ...] = (
    "created_at",
    "last_updated_at",
    "complaint_id",
    "title",
    "status",
    "category",
)
