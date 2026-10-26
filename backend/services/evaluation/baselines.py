"""Label-free comparison baselines for the Phase D2.1 evaluation harness.

These helpers produce deterministic orderings/measurements only. They never
compute accuracy, precision or recall, because the project has no ground-truth
recurrence, relevance or resolution labels. Any label-dependent metric must be
reported separately as "not evaluated".

A *record* is a plain ``dict`` with (at least) these keys:

    complaint_id, category, created_at, latitude, longitude,
    status, severity_label, resolved_at

Extra keys are ignored. ``created_at``/``resolved_at`` are naive datetimes.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from backend.core.time import to_naive_utc
from backend.services.imports.duplicates import haversine_meters
from backend.services.prioritization.rule_based import (
    PrioritizationInputs,
    RuleBasedPrioritizer,
)
from backend.services.recurrence.types import (
    LOCATION_METERS_MAX,
    TIME_WINDOW_DAYS,
)

_EPOCH = datetime.min


@dataclass
class Baseline:
    name: str
    key: str


class Baselines:
    FIFO = Baseline(name="First-in-first-out", key="fifo")
    COMPLAINT_COUNT = Baseline(name="Complaint-count ranking", key="complaint_count")
    RULE_BASED = Baseline(name="Rule-based priority", key="rule_based")


@dataclass
class ComparisonResult:
    method: str
    metrics: Dict[str, Any]
    eligible_count: int
    notes: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _sort_key(created_at: Optional[datetime]) -> datetime:
    return to_naive_utc(created_at) if created_at is not None else _EPOCH


def _category_key(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def fifo_ranking(records: List[dict]) -> List[str]:
    """Oldest-first ordering (ties broken by complaint_id for determinism)."""
    ordered = sorted(
        records,
        key=lambda r: (_sort_key(r.get("created_at")), r["complaint_id"]),
    )
    return [r["complaint_id"] for r in ordered]


def count_based_ranking(
    records: List[dict],
    *,
    meters: float = LOCATION_METERS_MAX,
    days: float = TIME_WINDOW_DAYS,
) -> Tuple[List[str], Dict[str, int]]:
    """Rank by the number of same-category nearby-and-recent other complaints.

    Two complaints contribute to each other's count when *all* of: same
    (case-insensitive) category, valid coordinates within ``meters``, and
    timestamps within ``days``. Records missing category/coordinates/time receive
    a count of zero. Ties fall back to FIFO order.
    """
    counts: Dict[str, int] = {}
    for a in records:
        count = 0
        for b in records:
            if a is b:
                continue
            if _category_key(a.get("category")) == "" or _category_key(a.get("category")) != _category_key(b.get("category")):
                continue
            if None in (
                a.get("latitude"),
                a.get("longitude"),
                b.get("latitude"),
                b.get("longitude"),
            ):
                continue
            if a.get("created_at") is None or b.get("created_at") is None:
                continue
            distance = haversine_meters(
                a["latitude"], a["longitude"], b["latitude"], b["longitude"]
            )
            if distance > meters:
                continue
            delta_days = abs(
                (
                    to_naive_utc(a["created_at"]) - to_naive_utc(b["created_at"])
                ).total_seconds()
            ) / 86400.0
            if delta_days > days:
                continue
            count += 1
        counts[a["complaint_id"]] = count

    ordered = sorted(
        records,
        key=lambda r: (
            -counts[r["complaint_id"]],
            _sort_key(r.get("created_at")),
            r["complaint_id"],
        ),
    )
    return [r["complaint_id"] for r in ordered], counts


def rule_based_ranking(
    records: List[dict],
) -> Tuple[List[str], Dict[str, float]]:
    """Rank by the existing rule-based priority score.

    The scorer is invoked faithfully with the fields the data actually contains:

    - ``resolved_at`` is intentionally left ``None`` so unresolved-age evaluation
      cannot leak future/resolution information.
    - ``recurrence_evidence`` and ``evidence_completeness`` keep the scorer's
      documented defaults (no validated recurrence evidence / completeness
      estimate is assumed).
    - ``severity_label`` is passed through verbatim when present.

    Note the documented scorer behaviour: with ``resolved_at`` unset the age
    component is computed from ``created_at`` to ``created_at`` and therefore
    evaluates to 0. Ties fall back to FIFO order.
    """
    prioritizer = RuleBasedPrioritizer()
    scores: Dict[str, float] = {}
    for record in records:
        inputs = PrioritizationInputs(
            complaint_id=record["complaint_id"],
            category=record.get("category"),
            created_at=record.get("created_at"),
            resolved_at=None,
            status=record.get("status"),
            latitude=record.get("latitude"),
            longitude=record.get("longitude"),
            severity_label=record.get("severity_label"),
        )
        scores[record["complaint_id"]] = prioritizer.score(inputs).priority_score

    ordered = sorted(
        records,
        key=lambda r: (
            -scores[r["complaint_id"]],
            _sort_key(r.get("created_at")),
            r["complaint_id"],
        ),
    )
    return [r["complaint_id"] for r in ordered], scores
