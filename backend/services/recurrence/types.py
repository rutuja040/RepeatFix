"""Recurrence analysis types and configuration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, field_serializer

from backend.core.time import serialize_utc

# Configurable thresholds (documented, conservative baseline)
SIMILARITY_THRESHOLD = 0.7  # Lower than exact duplicate (0.85) to be inclusive, but never sufficient alone
MIN_SIMILARITY_WITH_LOCATION_OR_CATEGORY = 0.6  # minimum when other evidence exists
LOCATION_METERS_MAX = 200.0  # max distance to count as nearby
LOCATION_METERS_MIN = 0.0  # allow close proximity
TIME_WINDOW_DAYS = 30.0  # typical recurrence window
TEXT_REQUIRED_FOR_CANDIDATE = 0.6  # require some text similarity for non-exact-source relations


@dataclass
class ComplaintInput:
    """Normalized inputs for recurrence analysis."""

    complaint_id: str
    title: str
    description: Optional[str]
    category: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    location_label: Optional[str]
    administrative_area: Optional[str]
    created_at: Optional[datetime]
    source: Optional[str]
    synthetic: bool
    data_quality_flags: Optional[str]
    raw_imported_values: Optional[str]


class RecurrenceReason(BaseModel):
    """Explainable reason for a potential relationship."""

    model_config = ConfigDict(extra="forbid")

    code: str  # e.g., text_similarity, nearby, same_category, same_admin_area
    description: str
    weight_contribution: float  # 0-1, explainable contribution
    evidence: Dict[str, Optional[str]] = {}


class RecurrenceRelation(BaseModel):
    """A potential recurrence relation (never confirmed automatically)."""

    model_config = ConfigDict(extra="forbid")

    related_complaint_id: str
    relation_type: str  # "exact_source", "potential_recurrence", "related_candidate"
    confidence_explained: float  # 0-1, sum of contributions; NOT a calibrated probability
    confidence_label: str  # "low", "medium", "high" (for UI; explicitly not probability)
    reasons: List[RecurrenceReason] = []
    evidence_summary: str
    requires_human_review: bool = True
    is_auto_merge_candidate: bool = False  # always False per requirements


class RecurrenceAnalysisResult(BaseModel):
    """Analysis result for a single complaint."""

    model_config = ConfigDict(extra="forbid")

    complaint_id: str
    analyzed_at: datetime
    relations: List[RecurrenceRelation] = []
    warnings: List[str] = []
    assumptions: List[str] = []
    has_reliable_coords: bool = False
    has_reliable_time: bool = False
    missing_data: List[str] = []

    @field_serializer("analyzed_at")
    def _serialize_analyzed_at(self, v: datetime) -> Optional[str]:
        return serialize_utc(v)
