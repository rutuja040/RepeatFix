"""Recurrence API schemas."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, field_serializer

from backend.core.time import serialize_utc
from backend.services.recurrence.types import RecurrenceAnalysisResult


class RecurrenceAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    complaint_id: str
    max_candidates: int = 50


class RecurrenceAnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    complaint_id: str
    analyzed_at: datetime
    relations: List[dict] = []
    warnings: List[str] = []
    assumptions: List[str] = []
    missing_data: List[str] = []
    has_reliable_coords: bool = False
    has_reliable_time: bool = False

    @field_serializer("analyzed_at")
    def _serialize_analyzed_at(self, v: datetime) -> Optional[str]:
        return serialize_utc(v)

    @classmethod
    def from_result(cls, res: RecurrenceAnalysisResult) -> "RecurrenceAnalysisResponse":
        return cls(
            complaint_id=res.complaint_id,
            analyzed_at=res.analyzed_at,
            relations=[r.model_dump() for r in res.relations],
            warnings=res.warnings,
            assumptions=res.assumptions,
            missing_data=res.missing_data,
            has_reliable_coords=res.has_reliable_coords,
            has_reliable_time=res.has_reliable_time,
        )
