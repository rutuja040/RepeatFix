"""Recurrence analysis service implementation (explainable baseline)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import List

from backend.core.time import to_naive_utc
from backend.services.imports.duplicates import (
    haversine_meters,
    normalize_text,
    text_similarity,
)
from backend.services.recurrence.types import (
    ComplaintInput,
    RecurrenceAnalysisResult,
    RecurrenceReason,
    RecurrenceRelation,
    SIMILARITY_THRESHOLD,
    TIME_WINDOW_DAYS,
    LOCATION_METERS_MAX,
)

_CONTRIB_TEXT = 0.4
_CONTRIB_LOCATION = 0.3
_CONTRIB_CATEGORY = 0.2
_CONTRIB_TIME = 0.1


class RecurrenceAnalysisService:
    """Service for analyzing potential recurrence relationships.

    - Never modifies complaints or history.
    - Never auto-merges or deletes.
    - Text similarity alone is insufficient.
    - All matches require explainable reasons.
    - Conservative when data missing.
    """

    def __init__(self) -> None:
        pass

    def analyze(
        self, complaint: ComplaintInput, candidates: List[ComplaintInput]
    ) -> RecurrenceAnalysisResult:
        """Analyze relationships between complaint and candidate complaints."""
        warnings: List[str] = []
        assumptions: List[str] = []
        missing_data: List[str] = []
        relations: List[RecurrenceRelation] = []

        has_reliable_coords = complaint.latitude is not None and complaint.longitude is not None
        has_reliable_time = complaint.created_at is not None

        if not has_reliable_coords:
            missing_data.append("coordinates")
            warnings.append("Missing reliable coordinates; location evidence treated conservatively.")
        if not has_reliable_time:
            missing_data.append("created_at")
            warnings.append("Missing reliable timestamps; time proximity evidence limited.")

        text_self = normalize_text(complaint.title, complaint.description or "")
        if not text_self:
            warnings.append("Complaint has little text content; similarity assessment limited.")

        for cand in candidates:
            if cand.complaint_id == complaint.complaint_id:
                continue

            # Check exact source relation via raw import values/source key if present
            if self._is_same_source_record(complaint, cand):
                relations.append(
                    self._build_exact_source_relation(complaint, cand)
                )
                continue

            reasons: List[RecurrenceReason] = []
            contrib_total = 0.0

            # Text similarity
            text_cand = normalize_text(cand.title, cand.description or "")
            sim = text_similarity(text_self, text_cand) if text_self or text_cand else 0.0
            if sim >= SIMILARITY_THRESHOLD:
                reasons.append(
                    RecurrenceReason(
                        code="text_similarity",
                        description=f"Text similarity {sim:.2f} >= {SIMILARITY_THRESHOLD:.2f}",
                        weight_contribution=_CONTRIB_TEXT,
                        evidence={
                            "similarity": f"{sim:.3f}",
                            "threshold": f"{SIMILARITY_THRESHOLD:.3f}",
                        },
                    )
                )
                contrib_total += _CONTRIB_TEXT

            # Category compatibility
            if complaint.category and cand.category:
                if complaint.category.strip().lower() == cand.category.strip().lower():
                    reasons.append(
                        RecurrenceReason(
                            code="same_category",
                            description="Same complaint category",
                            weight_contribution=_CONTRIB_CATEGORY,
                            evidence={
                                "category": complaint.category,
                                "related_category": cand.category,
                            },
                        )
                    )
                    contrib_total += _CONTRIB_CATEGORY
                else:
                    assumptions.append(f"Different categories for {complaint.complaint_id}/{cand.complaint_id}: not treated as same issue class.")

            # Location proximity
            loc_contribution = 0.0
            if (
                has_reliable_coords
                and cand.latitude is not None
                and cand.longitude is not None
            ):
                dist = haversine_meters(
                    complaint.latitude,  # type: ignore[arg-type]
                    complaint.longitude,  # type: ignore[arg-type]
                    cand.latitude,
                    cand.longitude,
                )
                if dist <= LOCATION_METERS_MAX:
                    loc_contribution = _CONTRIB_LOCATION
                    reasons.append(
                        RecurrenceReason(
                            code="nearby",
                            description=f"Located within {dist:.1f}m (<= {LOCATION_METERS_MAX:.1f}m)",
                            weight_contribution=loc_contribution,
                            evidence={
                                "distance_m": f"{dist:.2f}",
                                "max_m": f"{LOCATION_METERS_MAX:.2f}",
                            },
                        )
                    )
                    contrib_total += loc_contribution
            elif complaint.location_label or complaint.administrative_area or cand.location_label or cand.administrative_area:
                # Weak location evidence only; don't add strong contribution
                if self._admin_areas_match(complaint, cand):
                    reasons.append(
                        RecurrenceReason(
                            code="same_admin_area",
                            description="Same administrative area (text match)",
                            weight_contribution=0.05,
                            evidence={},
                        )
                    )
                    contrib_total += 0.05

            # Time proximity
            time_contribution = 0.0
            if has_reliable_time and cand.created_at is not None:
                delta_days = abs(
                    (to_naive_utc(complaint.created_at) - to_naive_utc(cand.created_at)).total_seconds()
                ) / 86400.0
                if delta_days <= TIME_WINDOW_DAYS:
                    time_contribution = _CONTRIB_TIME
                    reasons.append(
                        RecurrenceReason(
                            code="time_proximity",
                            description=f"Created within {delta_days:.1f} days (<= {TIME_WINDOW_DAYS:.1f}d)",
                            weight_contribution=time_contribution,
                            evidence={
                                "delta_days": f"{delta_days:.2f}",
                                "window_days": f"{TIME_WINDOW_DAYS:.2f}",
                            },
                        )
                    )
                    contrib_total += time_contribution

            # Conservative acceptance rule (Phase D1).
            # A potential recurrence requires multiple independent evidence types.
            # Text similarity alone is never sufficient, and weak admin-area
            # location must not substitute for validated geographic proximity.
            # Require text similarity, category compatibility, strong geographic
            # proximity and time proximity together; reject if any is missing.
            required_codes = {
                "text_similarity",
                "same_category",
                "nearby",
                "time_proximity",
            }
            if not required_codes.issubset({r.code for r in reasons}):
                continue

            confidence = min(1.0, contrib_total)
            confidence_label = self._label_confidence(confidence)
            evidence_summary = self._summarize_evidence(reasons)

            relations.append(
                RecurrenceRelation(
                    related_complaint_id=cand.complaint_id,
                    relation_type="potential_recurrence",
                    confidence_explained=round(confidence, 3),
                    confidence_label=confidence_label,
                    reasons=reasons,
                    evidence_summary=evidence_summary,
                    requires_human_review=True,
                    is_auto_merge_candidate=False,
                )
            )

        if not relations:
            assumptions.append("No strong recurrence evidence found with provided candidates.")

        return RecurrenceAnalysisResult(
            complaint_id=complaint.complaint_id,
            analyzed_at=datetime.utcnow(),
            relations=relations,
            warnings=warnings,
            assumptions=assumptions,
            has_reliable_coords=has_reliable_coords,
            has_reliable_time=has_reliable_time,
            missing_data=missing_data,
        )

    def _is_same_source_record(self, a: ComplaintInput, b: ComplaintInput) -> bool:
        try:
            ra = json.loads(a.raw_imported_values) if a.raw_imported_values else {}
            rb = json.loads(b.raw_imported_values) if b.raw_imported_values else {}
        except Exception:
            ra, rb = {}, {}
        if ra.get("source_key") and rb.get("source_key") and ra["source_key"] == rb["source_key"]:
            return True
        if a.source and b.source and a.source == b.source:
            if ra.get("source_record_id") and rb.get("source_record_id") and ra["source_record_id"] == rb["source_record_id"]:
                return True
        return False

    def _build_exact_source_relation(self, a: ComplaintInput, b: ComplaintInput) -> RecurrenceRelation:
        reason = RecurrenceReason(
            code="same_source_record",
            description="Same source record identity (same source_key/source_record_id)",
            weight_contribution=1.0,
            evidence={},
        )
        return RecurrenceRelation(
            related_complaint_id=b.complaint_id,
            relation_type="exact_source",
            confidence_explained=1.0,
            confidence_label="high",
            reasons=[reason],
            evidence_summary="Identical source identity; not a new incident.",
            requires_human_review=True,
            is_auto_merge_candidate=False,
        )

    def _admin_areas_match(self, a: ComplaintInput, b: ComplaintInput) -> bool:
        if not a.administrative_area or not b.administrative_area:
            return False
        return a.administrative_area.strip().lower() == b.administrative_area.strip().lower()

    def _label_confidence(self, c: float) -> str:
        if c >= 0.8:
            return "high"
        if c >= 0.5:
            return "medium"
        return "low"

    def _summarize_evidence(self, reasons: List[RecurrenceReason]) -> str:
        codes = sorted({r.code for r in reasons})
        return "Evidence: " + ", ".join(codes)
