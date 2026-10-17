from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, field_serializer

from backend.core.time import serialize_utc


class ImportIssue(BaseModel):
    """One row-level error, warning, or skip notice."""

    model_config = ConfigDict(extra="forbid")

    row_number: int
    field: Optional[str] = None
    code: str
    message: str
    status: str  # "rejected" | "flagged" | "skipped"


class ImportLimits(BaseModel):
    max_size_mb: int
    max_rows: int
    supported_types: List[str]


class ImportFieldInfo(BaseModel):
    name: str
    required: bool
    max_length: Optional[int] = None
    description: str
    aliases: List[str]


class ImportFieldsResponse(BaseModel):
    """Metadata that lets a client build a column-mapping UI."""

    fields: List[ImportFieldInfo]
    required_fields: List[str]
    limits: ImportLimits


class ImportSummary(BaseModel):
    """Result of a CSV import (or dry-run preview)."""

    model_config = ConfigDict(extra="forbid")

    status: str
    dry_run: bool
    total_rows: int
    accepted_rows: int
    rejected_rows: int
    skipped_rows: int
    flagged_rows: int
    exact_duplicate_rows: int
    potential_duplicate_rows: int
    issues_truncated: bool

    import_id: Optional[str] = None
    source_filename: Optional[str] = None
    source_label: Optional[str] = None
    dataset_identity: Optional[str] = None
    content_sha256: Optional[str] = None
    created_at: Optional[datetime] = None
    mapping: Dict[str, str] = {}
    detected_columns: List[str] = []
    unmatched_columns: List[str] = []
    ambiguous: Dict[str, List[str]] = {}
    missing_required: List[str] = []
    issues: List[ImportIssue] = []
    message: Optional[str] = None

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, v: Optional[datetime]) -> Optional[str]:
        return serialize_utc(v)


class ImportListItem(BaseModel):
    """Compact import-history row (no per-row issues)."""

    import_id: str
    status: str
    source_filename: Optional[str] = None
    source_label: Optional[str] = None
    dataset_identity: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_rows: int
    accepted_rows: int
    rejected_rows: int
    skipped_rows: int
    flagged_rows: int
    exact_duplicate_rows: int
    potential_duplicate_rows: int
    message: Optional[str] = None

    @field_serializer("created_at", "completed_at", when_used="json")
    def _serialize_dates(self, v: Optional[datetime]) -> Optional[str]:
        return serialize_utc(v)
