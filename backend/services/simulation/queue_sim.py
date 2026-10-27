"""Phase D3 discrete-event engine: single-server, non-preemptive FIFO queue.

Implements the event loop of design §3.1/§3.4 (D3.1 core; D3.2 adds the
observable-only snapshot mapping and the M6 outcome flag; D3.3 threads the
frozen arrival-snapshot evidence bundle through to policy snapshots and
enforces registry safety):

- next event is ``min(next arrival, next service completion)``;
- one server (``SimConfig.c == 1`` is enforced at config construction);
- when the server is free it selects the waiting complaint with the best
  policy rank — highest score, ties FIFO, then ``complaint_id`` — via
  :func:`backend.services.simulation.policies.selection_rank`;
- ties between a completion and an arrival at the same timestamp are
  resolved **completion before arrival** (deterministic; with a single
  FIFO queue this yields the same dispatch order as the reverse rule, and
  the choice is fixed here so runs are bit-reproducible);
- the loop stops at ``t_cut = t_end_obs + t_drain_max`` (design §6.4):
  events strictly after ``t_cut`` are never processed, so no complaint ever
  receives a start or completion beyond the cutoff. Complaints still
  waiting at ``t_cut`` stay with ``start = None``; a complaint in service at
  ``t_cut`` keeps ``start`` but ``completion = None`` (right-censoring,
  design §5).

Leakage and stream discipline (design §3.1.1, §6.2):

- the engine receives only an immutable :class:`ArrivalStream` — it has no
  RNG, no seed, and no generator access, so policy execution cannot consume
  or shift an exogenous stream;
- a stream containing an arrival after ``t_end_obs`` is rejected loudly
  (design §6.4: arrivals after ``t_end_obs`` are not generated);
- ``run_policy`` mutates nothing it is given: same (stream, config, policy)
  ⇒ identical :class:`RunResult` (design §6.2 determinism requirement).
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from backend.services.simulation.model import (
    ArrivalStream,
    ComplaintOutcome,
    PolicyEvidence,
    RunResult,
    SimComplaint,
    SimConfig,
)
from backend.services.simulation.policies import (
    EVIDENCE_BASED_POLICY_NAMES,
    Policy,
    PolicySnapshot,
    selection_rank,
    validate_policy,
)


def _snapshot_of(
    complaint: SimComplaint,
    evidence: Optional[PolicyEvidence] = None,
) -> PolicySnapshot:
    """Observable-only policy-visible snapshot (design §3.1.1, §3.6).

    Maps exactly the ``POLICY_VISIBLE_FIELDS`` allowlist of ``model.py`` —
    the engine never exposes ``latent_issue_id`` or
    ``sim_is_high_priority`` (or any ``sim_``/``latent_`` field) to a
    policy, regardless of which policy is registered. The two derived
    evidence fields (§3.6 table row 2) come exclusively from the frozen
    arrival-snapshot bundle computed *before* any policy ran — never from
    latent truth, never from future complaints (§3.4, §3.5). Ground-truth
    isolation is asserted by test §8.4.10.
    """
    snapshot_count = 0
    d1_count = 0
    if evidence is not None:
        snapshot_count = int(
            evidence.snapshot_counts.get(complaint.complaint_id, 0)
        )
        d1_count = int(
            evidence.d1_evidence_counts.get(complaint.complaint_id, 0)
        )
    return PolicySnapshot(
        complaint_id=complaint.complaint_id,
        created_at=complaint.arrival,
        title=complaint.title,
        description=complaint.description,
        category=complaint.category,
        latitude=complaint.latitude,
        longitude=complaint.longitude,
        location_label=complaint.location_label,
        administrative_area=complaint.administrative_area,
        status=complaint.status,
        source=complaint.source,
        synthetic=complaint.synthetic,
        snapshot_count=snapshot_count,
        d1_evidence_count=d1_count,
    )


def _dispatch(
    queue: "deque[SimComplaint]",
    policy: Policy,
    at: datetime,
    starts: Dict[str, Optional[datetime]],
    evidence: Optional[PolicyEvidence],
) -> Optional[Tuple[datetime, str]]:
    """Serve the best-ranked waiting complaint at time ``at`` (queue must be
    non-empty and the server idle). Records the service start and returns
    the resulting completion event."""
    if not queue:
        return None
    best = queue[0]
    best_rank = selection_rank(policy, _snapshot_of(best, evidence))
    for candidate in list(queue)[1:]:
        rank = selection_rank(policy, _snapshot_of(candidate, evidence))
        if rank < best_rank:
            best = candidate
            best_rank = rank
    queue.remove(best)
    starts[best.complaint_id] = at
    return (at + timedelta(days=best.service_days), best.complaint_id)


def run_policy(
    stream: ArrivalStream,
    config: SimConfig,
    policy: Policy,
    *,
    evidence: Optional[PolicyEvidence] = None,
) -> RunResult:
    """Run one policy over one generated stream; deterministic, RNG-free.

    Returns per-complaint arrival/start/completion records for **every**
    complaint in the stream (started and censored alike), in stream order,
    plus the enforced cutoff timestamp.

    ``evidence`` is the frozen arrival-snapshot bundle shared by every
    policy in a paired replication (design §3.5, §6.5). ``count_based``
    and ``recurrence_aware`` require it and raise when it is absent;
    ``fifo``/``rule_based`` do not consume it. ``policy`` must be a
    factory-stamped instance of a registered policy (registry safety,
    approved-plan §3) — hand-built ``Policy`` objects and unknown names
    are rejected.
    """
    validate_policy(policy)
    if policy.name == "recurrence_aware":
        # The saturation CAP is a config knob that travels with the policy
        # (design 4.4). A policy bound to a different CAP than the run
        # config — or one that does not declare a CAP at all — would
        # silently measure a different policy than the report claims, so
        # the mismatch is rejected loudly. The check runs before the
        # evidence-presence check: a policy that is wrong for this run
        # config is wrong regardless of the bundle it was handed.
        if policy.evidence_cap is None:
            raise ValueError(
                "policy 'recurrence_aware' does not declare its evidence_cap "
                "(CAP); bind the policy with policies_for_config(config) so "
                "the CAP travels with the policy (design 4.4)"
            )
        if float(policy.evidence_cap) != float(config.evidence_cap):
            raise ValueError(
                f"policy 'recurrence_aware' carries evidence_cap="
                f"{policy.evidence_cap} but the run config has "
                f"evidence_cap={config.evidence_cap}; bind the policy with "
                "policies_for_config(config) so the CAP travels with the "
                "policy (design 4.4)"
            )
    if policy.name in EVIDENCE_BASED_POLICY_NAMES and evidence is None:
        raise ValueError(
            f"policy {policy.name!r} requires the frozen arrival-snapshot "
            "evidence bundle (design 4.3/4.4); pass evidence=PolicyEvidence("
            "snapshot_counts=..., d1_evidence_counts=...) computed once per "
            "replication before any policy runs"
        )

    horizon_end = config.day_to_datetime(config.t_end_obs_days)
    t_cut = config.day_to_datetime(config.t_cut_days)
    for complaint in stream.complaints:
        if complaint.arrival > horizon_end:
            raise ValueError(
                "stream contains an arrival after t_end_obs "
                f"({complaint.complaint_id} @ {complaint.arrival.isoformat()}); "
                "design §6.4 requires arrivals only on [0, t_end_obs)"
            )

    starts: Dict[str, Optional[datetime]] = {
        c.complaint_id: None for c in stream.complaints
    }
    completions: Dict[str, Optional[datetime]] = {
        c.complaint_id: None for c in stream.complaints
    }

    queue: "deque[SimComplaint]" = deque()
    next_completion: Optional[Tuple[datetime, str]] = None
    arrival_index = 0
    arrivals = stream.complaints

    while True:
        next_arrival: Optional[datetime] = (
            arrivals[arrival_index].arrival if arrival_index < len(arrivals) else None
        )
        next_completion_at: Optional[datetime] = (
            next_completion[0] if next_completion is not None else None
        )
        if next_arrival is None and next_completion_at is None:
            break
        if next_completion_at is not None and (
            next_arrival is None or next_completion_at <= next_arrival
        ):
            event_at = next_completion_at
            event_kind = "completion"
        else:
            event_at = next_arrival
            event_kind = "arrival"
        if event_at is None or event_at > t_cut:
            break

        if event_kind == "completion":
            if next_completion is None:
                break
            completions[next_completion[1]] = event_at
            next_completion = None
            if queue:
                next_completion = _dispatch(queue, policy, event_at, starts, evidence)
        else:
            complaint = arrivals[arrival_index]
            arrival_index += 1
            queue.append(complaint)
            if next_completion is None:
                next_completion = _dispatch(queue, policy, event_at, starts, evidence)

    outcomes: List[ComplaintOutcome] = [
        ComplaintOutcome(
            complaint_id=complaint.complaint_id,
            arrival=complaint.arrival,
            start=starts[complaint.complaint_id],
            completion=completions[complaint.complaint_id],
            service_days=complaint.service_days,
            # Copied verbatim for the metrics layer's M6 subset (design §5);
            # the engine never uses it for ordering, so policies remain
            # blind to the latent designation (§3.1.1, test §8.4.10).
            sim_is_high_priority=complaint.sim_is_high_priority,
        )
        for complaint in arrivals
    ]

    return RunResult(
        policy=policy.name,
        master_seed=stream.master_seed,
        cell_id=stream.cell_id,
        seed=stream.seed,
        stopped_at=t_cut,
        outcomes=tuple(outcomes),
    )
