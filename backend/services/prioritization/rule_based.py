from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _days_between(start: Any, end: Any) -> float:
    try:
        from datetime import datetime

        if start is None or end is None:
            return 0.0
        s = start if isinstance(start, datetime) else datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        e = end if isinstance(end, datetime) else datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        delta = (e - s).total_seconds() / 86400.0
        return max(0.0, delta)
    except Exception:
        return 0.0


@dataclass
class PrioritizationInputs:
    complaint_id: str
    category: Optional[str] = None
    created_at: Optional[Any] = None
    resolved_at: Optional[Any] = None
    status: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    severity_label: Optional[str] = None
    evidence_completeness: float = 0.5  # 0-1
    recurrence_evidence: float = 0.0  # 0-1
    uncertainty_flags: List[str] = None

    def __post_init__(self):
        if self.uncertainty_flags is None:
            self.uncertainty_flags = []


@dataclass
class PrioritizationResult:
    complaint_id: str
    priority_score: float  # 0-1 (higher = higher priority)
    components: Dict[str, float]
    rationale: List[str]
    uncertainty_flags: List[str]
    data_quality_warnings: List[str]
    missing_fields: List[str]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


class RuleBasedPrioritizer:
    """
    Transparent, deterministic baseline prioritizer.
    Does NOT treat missing severity/recurrence as low severity.
    Explicitly flags uncertainty when evidence is insufficient.
    """

    def score(self, inputs: PrioritizationInputs) -> PrioritizationResult:
        components: Dict[str, float] = {}
        rationale: List[str] = []
        uncertainty_flags: List[str] = list(inputs.uncertainty_flags or [])
        data_quality_warnings: List[str] = []
        missing: List[str] = []

        # Severity (0-1)
        sev = 0.0
        if inputs.severity_label:
            sl = str(inputs.severity_label).lower()
            if sl in ("critical", "high", "severe"):
                sev = 0.9
            elif sl in ("medium", "moderate"):
                sev = 0.6
            elif sl in ("low", "minor"):
                sev = 0.3
            else:
                sev = 0.5
                data_quality_warnings.append("unrecognized_severity_label")
        else:
            sev = 0.0
            missing.append("severity_label")
            uncertainty_flags.append("missing_severity")

        components["severity"] = round(sev, 4)

        # Age (unresolved days) 0-1
        now_like = inputs.created_at
        end_ref = inputs.resolved_at if inputs.status == "resolved" and inputs.resolved_at else None
        age_days = _days_between(inputs.created_at, end_ref) if end_ref else _days_between(inputs.created_at, now_like)
        age_norm = min(age_days / 30.0, 1.0) if age_days >= 0 else 0.0
        components["age_unresolved_days_norm"] = round(age_norm, 4)
        if age_days > 14:
            rationale.append(f"unresolved > {int(age_days)}d")

        # Recurrence evidence 0-1
        rec = max(0.0, min(_to_float(inputs.recurrence_evidence, 0.0), 1.0))
        components["recurrence_evidence"] = round(rec, 4)
        if rec < 0.1 and "recurrence" not in uncertainty_flags and inputs.recurrence_evidence is None:
            uncertainty_flags.append("missing_recurrence_evidence")

        # Completeness 0-1 (higher better evidence)
        comp = max(0.0, min(_to_float(inputs.evidence_completeness, 0.5), 1.0))
        components["evidence_completeness"] = round(comp, 4)
        if comp < 0.4:
            uncertainty_flags.append("low_evidence_completeness")
            data_quality_warnings.append("low_evidence_completeness")

        # Location completeness
        has_geo = inputs.latitude is not None and inputs.longitude is not None
        loc = 1.0 if has_geo else 0.0
        components["location_completeness"] = round(loc, 4)
        if not has_geo:
            missing.append("location")
            uncertainty_flags.append("missing_location")

        # Base weighted (transparent, tunable; not claimed optimal)
        score = (
            0.40 * sev
            + 0.25 * age_norm
            + 0.20 * rec
            + 0.10 * comp
            + 0.05 * loc
        )
        score = max(0.0, min(score, 1.0))
        components["base_weighted_sum"] = round(score, 4)

        # Penalize high uncertainty - do not invent certainty
        if uncertainty_flags:
            penalty = min(0.15, len(set(uncertainty_flags)) * 0.03)
            score = max(0.0, score - penalty)
            components["uncertainty_penalty"] = round(penalty, 4)
            rationale.append("uncertainty_penalty_applied")

        components["final_raw"] = round(score, 4)

        # Manual review if insufficient evidence overall
        if sev < 0.1 and rec < 0.1 and comp < 0.5:
            uncertainty_flags.append("manual_review_recommended")
            rationale.append("insufficient_evidence_manual_review")

        # Deduplicate
        uncertainty_flags = sorted(set(uncertainty_flags))
        data_quality_warnings = sorted(set(data_quality_warnings))
        missing = sorted(set(missing))

        result = PrioritizationResult(
            complaint_id=inputs.complaint_id,
            priority_score=round(score, 4),
            components=components,
            rationale=rationale or ["baseline_rule_based"],
            uncertainty_flags=uncertainty_flags,
            data_quality_warnings=data_quality_warnings,
            missing_fields=missing,
        )
        return result
