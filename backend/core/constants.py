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

# CSV import status values (see docs/data_import.md).
IMPORT_STATUS_COMPLETED: str = "completed"
IMPORT_STATUS_EMPTY: str = "empty"
IMPORT_STATUS_FAILED: str = "failed"

IMPORT_STATUSES: tuple[str, ...] = (
    IMPORT_STATUS_COMPLETED,
    IMPORT_STATUS_EMPTY,
    IMPORT_STATUS_FAILED,
)
