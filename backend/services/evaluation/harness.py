"""Phase D2.1 label-free evaluation harness.

The harness is **read-only** with respect to complaints. It:

1. selects a temporal as-of snapshot (never using complaints created after the
   evaluation cutoff, and never using ``resolved_at``),
2. compares deterministic label-free baselines (FIFO, complaint-count,
   rule-based) on an identical eligible set,
3. runs a signal ablation of the existing recurrence service,
4. reports descriptive counts, missing-data rates and agreement statistics.

It deliberately does **not** fabricate labels or compute accuracy/precision/
recall/calibration. Every label-dependent metric is emitted as
``{"status": "not_evaluated", "reason": ...}``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from sqlalchemy.orm import Session

from backend.core.time import to_naive_utc, utcnow
from backend.models.complaint import Complaint, ComplaintHistory
from backend.models.import_batch import ImportBatch
from backend.services.evaluation.baselines import (
    Baselines,
    count_based_ranking,
    fifo_ranking,
    rule_based_ranking,
)
from backend.services.recurrence.service import (
    REQUIRED_SIGNALS,
    RecurrenceAnalysisService,
)
from backend.services.recurrence.types import (
    ComplaintInput,
    LOCATION_METERS_MAX,
    SIMILARITY_THRESHOLD,
    TIME_WINDOW_DAYS,
)

HARNESS_VERSION = "d2.1"


class _AblationRecurrenceService(RecurrenceAnalysisService):
    """Evaluation-only recurrence service with an ablated acceptance rule.

    Signal ablation is a *harness* concern, so it lives here rather than in
    the public production API: the production
    :meth:`RecurrenceAnalysisService.analyze` keeps the Phase D1 four-signal
    rule and exposes no way to weaken it. This subclass narrows the set of
    required signals through the protected ``_required_signals`` hook. Every
    signal is still computed and reported; an ablated signal simply stops
    being *required* for acceptance.
    """

    def __init__(self, disabled_signals: Set[str]) -> None:
        super().__init__()
        self._disabled_signals = frozenset(disabled_signals)

    def _required_signals(self) -> FrozenSet[str]:
        return REQUIRED_SIGNALS - self._disabled_signals


# Fields whose presence/absence is summarised as a missing-data rate.
MISSING_DATA_FIELDS: Tuple[str, ...] = (
    "description",
    "category",
    "latitude",
    "longitude",
    "location_label",
    "administrative_area",
    "created_at",
    "resolved_at",
    "severity_label",
    "assigned_department",
    "source",
)

# A record is eligible for *ranking* baselines when it has a usable timestamp.
RANKING_ELIGIBILITY = "created_at is present and <= cutoff (when a cutoff is set)"
# A record is eligible for *recurrence* when it also has coordinates + category.
RECURRENCE_ELIGIBILITY = (
    "ranking-eligible AND latitude/longitude AND category are present"
)

# Signal ablations: (config name, signals removed from the acceptance rule).
ABLATION_CONFIGS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("full", ()),
    ("without_text_similarity", ("text_similarity",)),
    ("without_same_category", ("same_category",)),
    ("without_nearby", ("nearby",)),
    ("without_time_proximity", ("time_proximity",)),
)

_LABEL_DEPENDENT_REASONS: Tuple[Dict[str, str], ...] = (
    {
        "metric": "recurrence_precision_recall_f1",
        "reason": "No ground-truth recurrence/duplicate pair labels exist.",
    },
    {
        "metric": "precision_at_k / recall_at_k / ndcg_at_k",
        "reason": "No relevance labels exist to rank against.",
    },
    {
        "metric": "resolution_time_mae / rmse",
        "reason": (
            "resolved_at is not importable and too few resolved records exist; "
            "no reliable ground truth."
        ),
    },
    {
        "metric": "calibration / brier_score",
        "reason": (
            "Recurrence confidence_explained is an explainable score, not a "
            "calibrated probability, and no outcome labels exist."
        ),
    },
    {
        "metric": "resource_allocation / staffing simulation",
        "reason": "No resource, capacity or staffing fields exist in the data.",
    },
)


@dataclass
class EvaluationConfig:
    """Configuration for a single harness run."""

    cutoff: Optional[datetime] = None
    max_candidates: int = 50
    include_synthetic: bool = False
    top_k: int = 20
    run_timestamp: Optional[datetime] = None
    code_commit: Optional[str] = None
    db_description: Optional[str] = None
    ablation_configs: Sequence[Tuple[str, Tuple[str, ...]]] = ABLATION_CONFIGS

    def to_dict(self, cutoff: Optional[datetime]) -> Dict[str, Any]:
        return {
            "cutoff": _iso(cutoff),
            "max_candidates": self.max_candidates,
            "include_synthetic": self.include_synthetic,
            "top_k": self.top_k,
            "ranking_eligibility": RANKING_ELIGIBILITY,
            "recurrence_eligibility": RECURRENCE_ELIGIBILITY,
            "required_recurrence_signals": sorted(REQUIRED_SIGNALS),
            # Numeric thresholds required to reproduce the experiment. These
            # are the live production constants (imported, never re-typed), so
            # a report can always be matched against the policy that produced
            # it.
            "recurrence_thresholds": {
                "similarity_threshold": SIMILARITY_THRESHOLD,
                "location_meters_max": LOCATION_METERS_MAX,
                "time_window_days": TIME_WINDOW_DAYS,
            },
            "count_based_thresholds": {
                "meters": LOCATION_METERS_MAX,
                "days": TIME_WINDOW_DAYS,
            },
            "ablation_configs": [
                {"name": name, "disabled_signals": list(sigs)}
                for name, sigs in self.ablation_configs
            ],
        }


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return to_naive_utc(value).isoformat()


def _record_from_orm(row: Complaint) -> Dict[str, Any]:
    return {
        "complaint_id": row.complaint_id,
        "title": row.title,
        "description": row.description,
        "category": row.category,
        "latitude": row.latitude,
        "longitude": row.longitude,
        "location_label": row.location_label,
        "administrative_area": row.administrative_area,
        "created_at": to_naive_utc(row.created_at) if row.created_at is not None else None,
        "resolved_at": to_naive_utc(row.resolved_at) if row.resolved_at is not None else None,
        "status": row.status,
        "assigned_department": row.assigned_department,
        "source": row.source,
        "synthetic": bool(row.synthetic),
        "severity_label": row.severity_label,
        "data_quality_flags": row.data_quality_flags,
        "raw_imported_values": row.raw_imported_values,
    }


def load_records(db: Session) -> List[Dict[str, Any]]:
    """Load all complaints deterministically ordered (created_at, complaint_id)."""
    rows = (
        db.query(Complaint)
        .order_by(Complaint.created_at.asc(), Complaint.complaint_id.asc())
        .all()
    )
    return [_record_from_orm(r) for r in rows]


def _max_created_at(records: Sequence[Dict[str, Any]]) -> Optional[datetime]:
    values = [r["created_at"] for r in records if r["created_at"] is not None]
    return max(values) if values else None


def _missing_rates(records: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    total = len(records)
    if total == 0:
        return {field_name: 0.0 for field_name in MISSING_DATA_FIELDS}
    rates: Dict[str, float] = {}
    for field_name in MISSING_DATA_FIELDS:
        missing = sum(1 for r in records if r.get(field_name) is None)
        rates[field_name] = round(missing / total, 4)
    return rates


def _to_complaint_input(record: Dict[str, Any]) -> ComplaintInput:
    return ComplaintInput(
        complaint_id=record["complaint_id"],
        title=record.get("title") or "",
        description=record.get("description"),
        category=record.get("category"),
        latitude=record.get("latitude"),
        longitude=record.get("longitude"),
        location_label=record.get("location_label"),
        administrative_area=record.get("administrative_area"),
        created_at=record.get("created_at"),
        source=record.get("source"),
        synthetic=bool(record.get("synthetic")),
        data_quality_flags=record.get("data_quality_flags"),
        raw_imported_values=record.get("raw_imported_values"),
    )


def _select_candidates(
    subject: Dict[str, Any],
    pool: Sequence[Dict[str, Any]],
    max_candidates: int,
) -> List[Dict[str, Any]]:
    """Deterministic candidate selection: most recent first, then by id.

    Timestamps follow the project's naive-UTC convention (``to_naive_utc``) and
    are compared directly as datetimes. ``datetime.timestamp()`` is deliberately
    avoided: it interprets naive values in the host machine's local timezone,
    which makes ordering environment-dependent. Records with no ``created_at``
    sort last.
    """
    others = [r for r in pool if r["complaint_id"] != subject["complaint_id"]]
    # Ascending id first so the stable descending-time sort keeps id order
    # among equal timestamps.
    others.sort(key=lambda r: r["complaint_id"])
    others.sort(
        key=lambda r: to_naive_utc(r["created_at"]) if r["created_at"] else datetime.min,
        reverse=True,
    )
    return list(others[:max_candidates])


def _run_ablation(
    recurrence_eligible: Sequence[Dict[str, Any]],
    config: Sequence[Tuple[str, Tuple[str, ...]]],
    cutoff: Optional[datetime],
    max_candidates: int,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for name, disabled in config:
        disabled_set: Set[str] = set(disabled)
        service = _AblationRecurrenceService(disabled_set)
        pairs: Set[Tuple[str, str]] = set()
        relation_counts: List[int] = []
        evidence_code_freq: Dict[str, int] = {}
        exact_source_total = 0

        for subject in recurrence_eligible:
            candidates = _select_candidates(subject, recurrence_eligible, max_candidates)
            analysis = service.analyze(
                _to_complaint_input(subject),
                [_to_complaint_input(c) for c in candidates],
                cutoff=cutoff,
            )
            relation_counts.append(len(analysis.relations))
            for relation in analysis.relations:
                pair = tuple(sorted((subject["complaint_id"], relation.related_complaint_id)))
                pairs.add(pair)
                if relation.relation_type == "exact_source":
                    exact_source_total += 1
                for reason in relation.reasons:
                    evidence_code_freq[reason.code] = evidence_code_freq.get(reason.code, 0) + 1

        total = len(relation_counts)
        with_relations = sum(1 for c in relation_counts if c > 0)
        results.append(
            {
                "name": name,
                "disabled_signals": list(disabled),
                "relations_total": sum(relation_counts),
                "complaints_analyzed": total,
                "complaints_with_relations": with_relations,
                "coverage": round(with_relations / total, 4) if total else 0.0,
                "relation_count_min": min(relation_counts) if relation_counts else 0,
                "relation_count_max": max(relation_counts) if relation_counts else 0,
                "relation_count_mean": (
                    round(sum(relation_counts) / total, 4) if total else 0.0
                ),
                "exact_source_relations": exact_source_total,
                "evidence_code_frequency": dict(sorted(evidence_code_freq.items())),
                "_pair_set": pairs,
            }
        )
    return results


def _agreement(a: Set[Tuple[str, str]], b: Set[Tuple[str, str]]) -> Dict[str, Any]:
    intersection = a & b
    union = a | b
    if union:
        jaccard: Optional[float] = round(len(intersection) / len(union), 4)
        status = "computed"
    else:
        # Jaccard is undefined (0/0) when both relation sets are empty.
        # Reporting 1.0 here would read as "perfect agreement", which is
        # misleading: no relations were produced, so there is nothing to
        # agree on. Represent it as null + an explicit status instead.
        jaccard = None
        status = "undefined_both_sets_empty"
    return {
        "shared": len(intersection),
        "only_in_a": len(a - b),
        "only_in_b": len(b - a),
        "jaccard": jaccard,
        "jaccard_status": status,
    }


def _ablation_agreement(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_name = {r["name"]: r["_pair_set"] for r in results}
    full = by_name.get("full", set())
    vs_full: Dict[str, Any] = {}
    for r in results:
        if r["name"] == "full":
            continue
        vs_full[r["name"]] = _agreement(r["_pair_set"], full)
    matrix: Dict[str, Dict[str, Optional[float]]] = {}
    names = [r["name"] for r in results]
    for name in names:
        matrix[name] = {
            other: (
                round(
                    len(by_name[name] & by_name[other])
                    / len(by_name[name] | by_name[other]),
                    4,
                )
                if (by_name[name] | by_name[other])
                # Undefined (0/0): both configs produced no relations.
                else None
            )
            for other in names
        }
    return {
        "vs_full": vs_full,
        "matrix_jaccard": matrix,
        "note": (
            "Agreement measures set overlap between recurrence configurations. "
            "It is NOT accuracy: without ground-truth pair labels a config that "
            "agrees with the full policy may be equally wrong. A null jaccard "
            "means the comparison is undefined because both configs produced "
            "zero relations (empty sets have no overlap to measure)."
        ),
    }


def _baseline_report(
    key: str,
    name: str,
    ranking: List[str],
    eligible_count: int,
    top_k: int,
    notes: List[str],
) -> Dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "eligible_count": eligible_count,
        "ranking_size": len(ranking),
        "ranking": ranking,
        "top_k": ranking[:top_k],
        "notes": notes,
        "performance_metrics": {
            entry["metric"]: {"status": "not_evaluated", "reason": entry["reason"]}
            for entry in _LABEL_DEPENDENT_REASONS
        },
    }


def run_evaluation(
    db: Session, config: Optional[EvaluationConfig] = None
) -> Dict[str, Any]:
    """Run the label-free evaluation and return a JSON-serialisable report."""
    config = config or EvaluationConfig()
    records = load_records(db)

    included = [r for r in records if config.include_synthetic or not r["synthetic"]]

    cutoff = (
        to_naive_utc(config.cutoff)
        if config.cutoff is not None
        else _max_created_at(included)
    )
    run_timestamp = (
        to_naive_utc(config.run_timestamp)
        if config.run_timestamp is not None
        else utcnow()
    )

    synthetic_excluded = sum(1 for r in records if r["synthetic"]) if not config.include_synthetic else 0

    def _after_cutoff(r: Dict[str, Any]) -> bool:
        return cutoff is not None and r["created_at"] is not None and r["created_at"] > cutoff

    ranking_eligible = [
        r
        for r in included
        if r["created_at"] is not None and not _after_cutoff(r)
    ]
    recurrence_eligible = [
        r
        for r in ranking_eligible
        if r.get("latitude") is not None
        and r.get("longitude") is not None
        and r.get("category")
    ]

    total_complaints = db.query(Complaint).count()
    total_import_batches = db.query(ImportBatch).count()
    total_history_rows = db.query(ComplaintHistory).count()

    snapshot = {
        "source": config.db_description or "database session",
        "total_complaints": total_complaints,
        "total_import_batches": total_import_batches,
        "total_complaint_history_rows": total_history_rows,
        "synthetic_complaints": sum(1 for r in records if r["synthetic"]),
        "created_at_min": _iso(_min_created_at(records)),
        "created_at_max": _iso(_max_created_at(records)),
    }

    counts = {
        "ranking_eligible": len(ranking_eligible),
        "recurrence_eligible": len(recurrence_eligible),
        "excluded": {
            "synthetic": synthetic_excluded,
            "no_created_at": sum(1 for r in included if r["created_at"] is None),
            "after_cutoff": sum(1 for r in included if _after_cutoff(r)),
            "recurrence_missing_coordinates_or_category": (
                len(ranking_eligible) - len(recurrence_eligible)
            ),
        },
    }

    missing_data = {
        "all_complaints": _missing_rates(records),
        "ranking_eligible": _missing_rates(ranking_eligible),
    }

    fifo_order = fifo_ranking(ranking_eligible)
    count_order, _count_scores = count_based_ranking(ranking_eligible)
    rule_order, _rule_scores = rule_based_ranking(ranking_eligible)

    baselines = [
        _baseline_report(
            Baselines.FIFO.key,
            Baselines.FIFO.name,
            fifo_order,
            len(ranking_eligible),
            config.top_k,
            ["Oldest complaints first; deterministic tie-break by complaint_id."],
        ),
        _baseline_report(
            Baselines.COMPLAINT_COUNT.key,
            Baselines.COMPLAINT_COUNT.name,
            count_order,
            len(ranking_eligible),
            config.top_k,
            [
                "Counts same-category complaints within "
                f"{LOCATION_METERS_MAX:g}m and {TIME_WINDOW_DAYS:g}d.",
                "Records missing coordinates/category/time receive a count of 0.",
            ],
        ),
        _baseline_report(
            Baselines.RULE_BASED.key,
            Baselines.RULE_BASED.name,
            rule_order,
            len(ranking_eligible),
            config.top_k,
            [
                "Existing RuleBasedPrioritizer invoked with resolved_at=None.",
                "recurrence_evidence and evidence_completeness use documented defaults.",
                "Age component evaluates to 0 for unresolved records (documented scorer behaviour).",
            ],
        ),
    ]

    ablation_raw = _run_ablation(
        recurrence_eligible, config.ablation_configs, cutoff, config.max_candidates
    )
    ablation = {
        "eligible_count": len(recurrence_eligible),
        "configs": [
            {k: v for k, v in r.items() if k != "_pair_set"} for r in ablation_raw
        ],
        "agreement": _ablation_agreement(ablation_raw),
    }

    report: Dict[str, Any] = {
        "metadata": {
            "harness_version": HARNESS_VERSION,
            "run_timestamp": _iso(run_timestamp),
            "code_commit": config.code_commit,
        },
        "snapshot": snapshot,
        "config": config.to_dict(cutoff),
        "counts": counts,
        "missing_data": missing_data,
        "baselines": baselines,
        "recurrence_ablation": ablation,
        "not_evaluated": [dict(entry) for entry in _LABEL_DEPENDENT_REASONS],
    }
    return report


def _min_created_at(records: Sequence[Dict[str, Any]]) -> Optional[datetime]:
    values = [r["created_at"] for r in records if r["created_at"] is not None]
    return min(values) if values else None
