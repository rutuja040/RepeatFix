"""Phase D3.2 leakage-free evidence snapshots via unmodified production code.

Implements the ``evidence.py`` slot of design §8.1 and the §8.3 adapter
decision (option (c)): a simulation-local adapter that runs the **real**
``RecurrenceAnalysisService`` on simulated complaints' observable fields and
computes predecessor-only snapshot counts with the production distance
function, category case-folding and window constants — all imported live,
never re-typed. This module does not modify production behaviour: it calls the
production API exactly as it stands (since the D3.3 remediation that API carries
no ``disabled_signals`` ablation parameter). Simulation runtime code
never imports the evaluation harness's private helpers (§8.3/§10.9 — parity
tests may use them as a frozen oracle).

Three independent safeguards keep evidence decision-time-safe (design §3.5):

1. **Pool filtering** — each complaint is analysed against its most recent
   ≤ ``config.max_candidates`` *eligible predecessors only* (arrived
   strictly before it under the deterministic ``(created_at, complaint_id)``
   key, with the harness ``RECURRENCE_ELIGIBILITY`` convention: ranking-
   eligible **and** coordinates **and** category present).
2. **Per-call cutoff** — every ``analyze`` call receives the complaint's own
   arrival time as the mandatory ``cutoff=`` keyword argument, so the
   service itself discards any candidate created after it.
3. **Ground-truth isolation** — only observable record fields (the
   ``SimComplaint.to_record()`` projection) enter production code; latent
   fields never do. A runtime check additionally raises if a relation ever
   references a non-predecessor, so removing any safeguard fails loudly.

Evidence mode is **arrival-snapshot** (design §3.1.1 default): the relation
list for complaint *i* is frozen at *i*'s arrival; every counterpart is a
predecessor, so filtering that list to ``arrival <= t`` (see
:func:`filter_relations_as_of`) is a defensive no-op assertion, not a
recompute. Dynamic recomputation (§6.3.4) is a later sensitivity item and
is not implemented here.

Exact-source relations are absent **by design** (§4.4 decision): the
generator never populates ``raw_imported_values`` provenance, so the
production ``exact_source`` path cannot fire on simulated data and Phase D3
evaluates exactly one evidence type — ``potential_recurrence``.

Counts (design §4.3/§8.3): :func:`snapshot_counts` implements
predecessor-only counting with the production conventions
(``count_based_ranking`` semantics restricted to predecessors — computing
counts over the complete stream would leak future data and is forbidden).
There is deliberately no saturation cap here; the §4.4 ``CAP`` constant
belongs to the recurrence-aware policy of the D3.3 milestone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Sequence, Tuple

from backend.core.time import to_naive_utc
from backend.services.imports.duplicates import haversine_meters
from backend.services.recurrence.service import RecurrenceAnalysisService
from backend.services.recurrence.types import (
    ComplaintInput,
    LOCATION_METERS_MAX,
    TIME_WINDOW_DAYS,
)
from backend.services.simulation.model import ArrivalStream, SimComplaint, SimConfig

_EPOCH = datetime.min


@dataclass(frozen=True)
class EvidenceRelation:
    """One D1 relation frozen into a complaint's arrival snapshot.

    A minimal, immutable projection of the production
    ``RecurrenceRelation`` (read-only reuse, design §8.3): enough for the
    evidence-count consumers of §4.4 without carrying production model
    objects around.
    """

    related_complaint_id: str
    relation_type: str
    confidence_explained: float
    reason_codes: Tuple[str, ...]


@dataclass(frozen=True)
class ComplaintEvidence:
    """Arrival-snapshot evidence for one complaint (design §3.5)."""

    complaint_id: str
    relations: Tuple[EvidenceRelation, ...]

    def evidence_count(self) -> int:
        """§4.4 count: ``potential_recurrence`` + ``exact_source`` relations.

        ``exact_source`` can never occur on simulated data (§4.4 decision),
        so this reduces in practice to the ``potential_recurrence`` count;
        the expression is kept unchanged so semantics match production
        should provenance ever exist.
        """
        return sum(
            1
            for relation in self.relations
            if relation.relation_type in ("potential_recurrence", "exact_source")
        )


def to_complaint_input(record: Dict[str, object]) -> ComplaintInput:
    """Simulation-local record→``ComplaintInput`` conversion (design §8.3).

    Re-implements the harness's ``_to_complaint_input`` semantics against
    the *public* ``ComplaintInput`` signature: ``title`` defaults to ``""``,
    optional fields via ``.get()``, ``synthetic`` coerced to bool. The
    harness helper is never imported by runtime code; a parity test (§8.4.16b)
    compares the two on the same inputs.
    """
    return ComplaintInput(
        complaint_id=str(record["complaint_id"]),
        title=record.get("title") or "",  # type: ignore[arg-type]
        description=record.get("description"),  # type: ignore[arg-type]
        category=record.get("category"),  # type: ignore[arg-type]
        latitude=record.get("latitude"),  # type: ignore[arg-type]
        longitude=record.get("longitude"),  # type: ignore[arg-type]
        location_label=record.get("location_label"),  # type: ignore[arg-type]
        administrative_area=record.get("administrative_area"),  # type: ignore[arg-type]
        created_at=record.get("created_at"),  # type: ignore[arg-type]
        source=record.get("source"),  # type: ignore[arg-type]
        synthetic=bool(record.get("synthetic")),  # type: ignore[arg-type]
        data_quality_flags=record.get("data_quality_flags"),  # type: ignore[arg-type]
        raw_imported_values=record.get("raw_imported_values"),  # type: ignore[arg-type]
    )


def is_recurrence_eligible(record: Dict[str, object]) -> bool:
    """Harness ``RECURRENCE_ELIGIBILITY`` convention (design §1, §8.3):

    ranking-eligible (has ``created_at``) **and** latitude/longitude **and**
    category present. Eligible complaints are the only ones that may appear
    in a candidate pool or contribute to a snapshot count.
    """
    if record.get("created_at") is None:
        return False
    if record.get("latitude") is None or record.get("longitude") is None:
        return False
    return bool(str(record.get("category") or "").strip())


def record_order_key(record: Dict[str, object]) -> Tuple[datetime, str]:
    """Deterministic predecessor key: (created_at asc, complaint_id asc).

    Missing ``created_at`` falls back to ``datetime.min`` (production
    ``baselines.py`` ordering convention); ties are broken by id so
    predecessor eligibility is deterministic even for equal timestamps.
    """
    created_at = record.get("created_at")
    naive = to_naive_utc(created_at) if created_at is not None else _EPOCH
    return (naive if naive is not None else _EPOCH, str(record["complaint_id"]))


def select_predecessors(
    subject: Dict[str, object],
    pool: Sequence[Dict[str, object]],
    max_candidates: int,
) -> List[Dict[str, object]]:
    """Deterministic ≤ ``max_candidates`` most-recent predecessor selection.

    Mirrors the harness ``_select_candidates`` ordering exactly (id ascending
    first, then a stable descending-``created_at`` sort so equal timestamps
    keep id order; ``datetime.timestamp()`` is deliberately avoided —
    naive-UTC datetimes are compared directly). The pool handed to this
    function must already contain only predecessors; the slice then takes
    the most recent ones.
    """
    others = [r for r in pool if r["complaint_id"] != subject["complaint_id"]]
    others.sort(key=lambda r: r["complaint_id"])
    others.sort(
        key=lambda r: to_naive_utc(r["created_at"]) if r["created_at"] else _EPOCH,
        reverse=True,
    )
    return list(others[:max_candidates])


def arrival_snapshot_evidence(
    stream: ArrivalStream,
    config: SimConfig,
) -> Dict[str, ComplaintEvidence]:
    """Frozen arrival-snapshot D1 evidence for every complaint in a stream.

    For each complaint *i* (any processing order; results are order-
    independent because the pools are), the eligible predecessors of *i*
    are selected under the deterministic key above, truncated to the
    ≤ ``config.max_candidates`` most recent ones, and fed to the
    unmodified ``RecurrenceAnalysisService`` with the mandatory
    ``cutoff=i.arrival``. Returns one immutable :class:`ComplaintEvidence`
    per complaint id.
    """
    service = RecurrenceAnalysisService()
    records = [complaint.to_record() for complaint in stream.complaints]
    ordered = sorted(records, key=record_order_key)
    positions = {str(r["complaint_id"]): i for i, r in enumerate(ordered)}

    evidence: Dict[str, ComplaintEvidence] = {}
    for complaint in stream.complaints:
        record = complaint.to_record()
        position = positions[complaint.complaint_id]
        predecessors = [
            r for r in ordered[:position] if is_recurrence_eligible(r)
        ]
        candidates = select_predecessors(
            record, predecessors, config.max_candidates
        )
        candidate_ids = {str(c["complaint_id"]) for c in candidates}
        analysis = service.analyze(
            to_complaint_input(record),
            [to_complaint_input(c) for c in candidates],
            cutoff=complaint.arrival,
        )
        relations: List[EvidenceRelation] = []
        for relation in analysis.relations:
            # Defence in depth (design §3.5): a relation to anything other
            # than a selected predecessor means a pool/cutoff safeguard has
            # been bypassed — fail loudly rather than store it.
            if relation.related_complaint_id not in candidate_ids:
                raise ValueError(
                    f"evidence leakage: {complaint.complaint_id} produced a "
                    f"relation to non-predecessor {relation.related_complaint_id}"
                )
            relations.append(
                EvidenceRelation(
                    related_complaint_id=relation.related_complaint_id,
                    relation_type=relation.relation_type,
                    confidence_explained=relation.confidence_explained,
                    reason_codes=tuple(r.code for r in relation.reasons),
                )
            )
        evidence[complaint.complaint_id] = ComplaintEvidence(
            complaint_id=complaint.complaint_id,
            relations=tuple(relations),
        )
    return evidence


def snapshot_counts(complaints: Sequence[SimComplaint]) -> Dict[str, int]:
    """Predecessor-only arrival-snapshot counts for every complaint.

    Implements the §4.3/§8.3 option (c) adapter: complaint *i*'s count is
    the number of *predecessors* (strictly before *i* under
    ``(created_at, complaint_id)``) within the production spatio-temporal-
    category window — same case-insensitive category, both coordinate pairs
    present, ``haversine_meters`` ≤ ``LOCATION_METERS_MAX`` (live import),
    ``|Δcreated_at|`` ≤ ``TIME_WINDOW_DAYS`` (live import) — exactly the
    pair criteria of ``count_based_ranking``, restricted to predecessors so
    no future complaint can influence a decision-time count (§4.3: full-
    stream counting is forbidden future-data leakage).

    Computed with a sliding window over the key-ordered complaints:
    O(n · k) where k is the number of predecessors inside the 30-day window.
    A complaint missing category/coordinates/``created_at`` scores 0 and
    contributes to no one — the documented production behaviour (§3.6).
    """
    ordered = sorted(complaints, key=lambda c: record_order_key(c.to_record()))
    active: List[SimComplaint] = []
    counts: Dict[str, int] = {}
    for subject in ordered:
        while (
            active
            and (subject.arrival - active[0].arrival).total_seconds() / 86400.0
            > TIME_WINDOW_DAYS
        ):
            active.pop(0)
        count = 0
        if (
            subject.category
            and subject.latitude is not None
            and subject.longitude is not None
        ):
            for other in active:
                if not other.category or other.latitude is None:
                    continue
                if other.longitude is None:
                    continue
                if subject.category.strip().lower() != other.category.strip().lower():
                    continue
                distance = haversine_meters(
                    subject.latitude,
                    subject.longitude,
                    other.latitude,
                    other.longitude,
                )
                if distance > LOCATION_METERS_MAX:
                    continue
                delta_days = abs(
                    (to_naive_utc(subject.arrival) - to_naive_utc(other.arrival)).total_seconds()
                ) / 86400.0
                if delta_days > TIME_WINDOW_DAYS:
                    continue
                count += 1
        counts[subject.complaint_id] = count
        active.append(subject)
    return counts


def filter_relations_as_of(
    evidence: ComplaintEvidence,
    arrival_by_id: Dict[str, datetime],
    t: datetime,
) -> Tuple[EvidenceRelation, ...]:
    """Defensive decision-time filter over a frozen arrival snapshot.

    Design §3.5: because every counterpart is a predecessor, all relations
    already satisfy ``arrival(counterpart) <= arrival(i) <= t`` for any
    decision time ``t >= arrival(i)`` — the filter is an assertion of that
    invariant, not a recompute, and never accesses future complaints.
    """
    t_naive = to_naive_utc(t)
    kept: List[EvidenceRelation] = []
    for relation in evidence.relations:
        counterpart_arrival = arrival_by_id.get(relation.related_complaint_id)
        if counterpart_arrival is None:
            continue
        naive = to_naive_utc(counterpart_arrival)
        if naive is not None and t_naive is not None and naive <= t_naive:
            kept.append(relation)
    return tuple(kept)
