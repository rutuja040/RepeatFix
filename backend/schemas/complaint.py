from typing import List, Optional
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from backend.core.constants import ComplaintStatus
from backend.core.time import serialize_utc

MAX_COMPLAINT_ID_LEN = 100
MAX_TITLE_LEN = 500
MAX_DESCRIPTION_LEN = 10000
MAX_CATEGORY_LEN = 200
MAX_LOCATION_LEN = 300
MAX_DEPARTMENT_LEN = 200
MAX_SOURCE_LEN = 300
MAX_SEVERITY_LEN = 100
MAX_EVIDENCE_LEN = 4000
MAX_FLAGS_LEN = 2000


def _non_blank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class ComplaintBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE_LEN)
    description: Optional[str] = Field(default=None, max_length=MAX_DESCRIPTION_LEN)
    category: Optional[str] = Field(default=None, max_length=MAX_CATEGORY_LEN)
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    location_label: Optional[str] = Field(default=None, max_length=MAX_LOCATION_LEN)
    administrative_area: Optional[str] = Field(default=None, max_length=MAX_LOCATION_LEN)
    status: ComplaintStatus = "open"
    assigned_department: Optional[str] = Field(default=None, max_length=MAX_DEPARTMENT_LEN)
    source: Optional[str] = Field(default=None, max_length=MAX_SOURCE_LEN)
    synthetic: bool = False
    evidence_metadata: Optional[str] = Field(default=None, max_length=MAX_EVIDENCE_LEN)
    severity_label: Optional[str] = Field(default=None, max_length=MAX_SEVERITY_LEN)
    data_quality_flags: Optional[str] = Field(default=None, max_length=MAX_FLAGS_LEN)

    @field_validator("title", mode="before")
    @classmethod
    def _title_not_blank(cls, v: object) -> object:
        if isinstance(v, str):
            return _non_blank(v)
        return v


class ComplaintCreate(ComplaintBase):
    """Create payload.

    ``created_at`` may be supplied by importers to preserve the original submission
    time of a source record. It is never accepted on update (see ComplaintUpdate).
    """

    complaint_id: str = Field(min_length=1, max_length=MAX_COMPLAINT_ID_LEN)
    created_at: Optional[datetime] = None

    @field_validator("complaint_id", mode="before")
    @classmethod
    def _complaint_id_not_blank(cls, v: object) -> object:
        if isinstance(v, str):
            return _non_blank(v)
        return v


class ComplaintUpdate(BaseModel):
    """Partial update payload for user-editable fields.

    Deliberately excluded (not user-editable):
    - ``complaint_id`` (identifier)
    - ``created_at`` (original creation time; must never be silently rewritten)
    - ``synthetic``, ``source``, ``raw_imported_values`` (provenance)
    - ``priority_score``, ``priority_rationale`` (system-generated predictions)
    """

    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, min_length=1, max_length=MAX_TITLE_LEN)
    description: Optional[str] = Field(default=None, max_length=MAX_DESCRIPTION_LEN)
    category: Optional[str] = Field(default=None, max_length=MAX_CATEGORY_LEN)
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    location_label: Optional[str] = Field(default=None, max_length=MAX_LOCATION_LEN)
    administrative_area: Optional[str] = Field(default=None, max_length=MAX_LOCATION_LEN)
    status: Optional[ComplaintStatus] = None
    assigned_department: Optional[str] = Field(default=None, max_length=MAX_DEPARTMENT_LEN)
    severity_label: Optional[str] = Field(default=None, max_length=MAX_SEVERITY_LEN)
    data_quality_flags: Optional[str] = Field(default=None, max_length=MAX_FLAGS_LEN)

    @field_validator("title", mode="before")
    @classmethod
    def _title_not_blank(cls, v: object) -> object:
        if v is None:
            raise ValueError("title cannot be null")
        if isinstance(v, str):
            return _non_blank(v)
        return v

    @field_validator("status", mode="before")
    @classmethod
    def _status_not_null(cls, v: object) -> object:
        if v is None:
            raise ValueError("status cannot be null")
        return v


class ComplaintInDBBase(ComplaintBase):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    complaint_id: str
    created_at: Optional[datetime] = None
    last_updated_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    evidence_metadata: Optional[str] = None
    raw_imported_values: Optional[str] = None
    priority_score: Optional[float] = None
    priority_rationale: Optional[str] = None

    @field_serializer("created_at", "last_updated_at", "resolved_at", when_used="json")
    def _serialize_dates(self, v: Optional[datetime]) -> Optional[str]:
        return serialize_utc(v)


class Complaint(ComplaintInDBBase):
    pass


class ComplaintHistoryItem(BaseModel):
    """One auditable change record for a complaint.

    ``complaint_id`` here is the external/human complaint identifier.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    complaint_id: str
    field_name: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    changed_at: Optional[datetime] = None
    changed_by: Optional[str] = None

    @field_serializer("changed_at", when_used="json")
    def _serialize_changed_at(self, v: Optional[datetime]) -> Optional[str]:
        return serialize_utc(v)


class ComplaintHistory(BaseModel):
    """Backwards-compatible alias used in earlier phases."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    field_name: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    changed_at: Optional[datetime] = None

    @field_serializer("changed_at", when_used="json")
    def _serialize_changed_at(self, v: Optional[datetime]) -> Optional[str]:
        return serialize_utc(v)


class CategoryCount(BaseModel):
    category: str
    count: int


class ComplaintMeta(BaseModel):
    """Reference data for filter UIs (backend is the source of truth)."""

    total: int
    statuses: List[str]
    categories: List[CategoryCount]
