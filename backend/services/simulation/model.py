"""Phase D3 simulation data model: typed config + immutable entities.

Implements the ``model.py`` slot of ``docs/phase_d3_simulation_design.md``
(§8.1). D3.1 established the config/entity foundation for the single-server
FIFO engine; D3.2 adds the generator knobs (``p_repeat``, ``p_high``,
``correlation_regime``, ``δ``, ``detector_visibility``, missingness rates),
observable complaint attributes, latent recurrence truth
(``latent_issue_id``/``sim_is_high_priority``), and the config echo of the
live production constants — every value here is a documented **[ASSUMPTION]**
(§7.3), not a calibrated real-world statistic.

Units and clocks (design §5: "All time quantities are in the configured unit
(days)"):

- Window, service, and horizon quantities are **days** as floats or as
  ``timedelta(days=...)``; ``SimConfig`` expresses them as ``*_days`` fields
  so no unit can be inferred implicitly.
- Absolute timestamps are **naive UTC datetimes** (project convention,
  ``backend/core/time.py``) anchored at ``SimConfig.epoch``. The epoch is an
  arbitrary simulation-clock origin recorded in config; it is not a claim
  about any real event date.

``mean_service_days`` (E[S]) defaults to 0.2 days per the corrected design
(§3.3). That value is an explicitly documented **placeholder** chosen to fix
the time scale — it is not calibrated to any real-world statistic and must
never be presented as one (design §3.3, §7.3). The same applies to
``p_repeat``, ``p_high``, and ``correlation_delta`` (§3.2, §3.7).

Field separation (design §3.6) is structural here: observable fields and
latent fields live on the same immutable :class:`SimComplaint`, but only
observable fields are ever projected (``to_record()`` for adapters,
:func:`backend.services.simulation.queue_sim._snapshot_of` for policies).
Latent fields are prefixed ``sim_``/``latent_`` and must never enter
policy-visible state (§3.1.1, §7.2); ground-truth isolation is asserted by
tests (§8.4.10).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, asdict, field, fields
from datetime import datetime, timedelta
from typing import Any, Dict, Mapping, Optional, Tuple

from backend.services.recurrence.service import REQUIRED_SIGNALS
from backend.services.recurrence.types import (
    LOCATION_METERS_MAX,
    SIMILARITY_THRESHOLD,
    TIME_WINDOW_DAYS,
)

_VALID_SERVICE_DISTS = ("exponential", "constant")
_VALID_CORRELATION_REGIMES = ("independent", "correlated", "anti_correlated")
_VALID_DETECTOR_VISIBILITIES = ("detector_visible", "detector_blind")

# Observable-only policy-visible field names (design §3.6 table row 1, plus
# the derived-evidence fields of row 2 that D3.3 policies consume). Kept as
# the explicit allowlist asserted by the ground-truth isolation tests
# (§8.4.10); latent/simulation-only fields must never appear here. The two
# derived fields are observed-only evidence (predecessor snapshot count and
# frozen arrival-snapshot D1 relation count) — never latent truth, never
# future-derived (§3.1.1, §3.5, §4.3–§4.4).
POLICY_VISIBLE_FIELDS: Tuple[str, ...] = (
    "complaint_id",
    "created_at",
    "title",
    "description",
    "category",
    "latitude",
    "longitude",
    "location_label",
    "administrative_area",
    "status",
    "source",
    "synthetic",
    "severity_label",
    "snapshot_count",
    "d1_evidence_count",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


@dataclass(frozen=True)
class SimConfig:
    """Immutable configuration for a simulation run (all time knobs in days).

    D3.3 completes the design §8.1 ``SimConfig`` knobs by adding the
    experiment-layer ``replications`` (R of §6.2) and the recurrence-aware
    evidence saturation constant ``evidence_cap`` (CAP of §4.4 — a
    simulation config constant, **not** a production constant, recorded in
    every report).
    """

    rho: float = 0.5
    c: int = 1
    mean_service_days: float = 0.2
    service_dist: str = "exponential"
    t_warm_days: float = 30.0
    t_obs_days: float = 180.0
    t_drain_max_days: float = 90.0
    max_arrivals: Optional[int] = None
    master_seed: int = 0
    epoch: datetime = datetime(2026, 1, 1)
    backlog_grid_step_days: float = 1.0
    # Generator knobs (design §3.2, §3.7) — [ASSUMPTION] defaults, not
    # calibrated to any dataset (§7.3); every report echoes their values.
    p_repeat: float = 0.3
    p_high: float = 0.2
    correlation_delta: float = 0.2
    correlation_regime: str = "independent"
    detector_visibility: str = "detector_visible"
    missing_coord_rate: float = 0.0
    missing_category_rate: float = 0.0
    # Candidate cap for the D1 arrival-snapshot evidence pool (design §3.5:
    # "max_candidates (= 50, production default)"). This is the production
    # default, not the §4.4 evidence saturation constant CAP.
    max_candidates: int = 50
    # Experiment-layer knobs (design §6.2, §4.4). ``replications`` is R,
    # the number of independent seeds per grid cell; ``evidence_cap`` is
    # CAP, the recurrence-evidence saturation constant (config-owned, not
    # production; echoed in every report per §6.7).
    replications: int = 30
    evidence_cap: float = 3.0

    def __post_init__(self) -> None:
        _require(isinstance(self.rho, (int, float)) and self.rho > 0,
                 "rho must be > 0 (design §3.2)")
        _require(self.c == 1,
                 "Phase D3 supports only c = 1 (single-server milestone, "
                 "design §3.1); multi-server sensitivity arrives later (§6.3 item 5)")
        _require(isinstance(self.mean_service_days, (int, float))
                 and self.mean_service_days > 0,
                 "mean_service_days (E[S]) must be > 0 (design §3.3)")
        _require(self.service_dist in _VALID_SERVICE_DISTS,
                 f"service_dist must be one of {_VALID_SERVICE_DISTS} (design §3.3)")
        _require(self.t_warm_days >= 0, "t_warm_days must be >= 0 (design §6.4)")
        _require(self.t_obs_days > 0, "t_obs_days must be > 0 (design §6.4)")
        _require(self.t_drain_max_days >= 0,
                 "t_drain_max_days must be >= 0 (design §6.4)")
        _require(self.max_arrivals is None or self.max_arrivals > 0,
                 "max_arrivals must be None or > 0 (design §6.8)")
        _require(self.backlog_grid_step_days > 0,
                 "backlog_grid_step_days must be > 0 (design §5, M4)")
        _require(isinstance(self.p_repeat, (int, float))
                 and 0.0 <= self.p_repeat <= 1.0,
                 "p_repeat must be in [0, 1] (design §3.2)")
        _require(isinstance(self.p_high, (int, float))
                 and 0.0 <= self.p_high <= 1.0,
                 "p_high must be in [0, 1] (design §3.7)")
        _require(isinstance(self.correlation_delta, (int, float))
                 and self.correlation_delta >= 0,
                 "correlation_delta (δ) must be >= 0 (design §3.7)")
        _require(self.correlation_regime in _VALID_CORRELATION_REGIMES,
                 f"correlation_regime must be one of {_VALID_CORRELATION_REGIMES} "
                 "(design §3.7)")
        _require(self.detector_visibility in _VALID_DETECTOR_VISIBILITIES,
                 f"detector_visibility must be one of {_VALID_DETECTOR_VISIBILITIES} "
                 "(design §3.5)")
        _require(isinstance(self.missing_coord_rate, (int, float))
                 and 0.0 <= self.missing_coord_rate <= 1.0,
                 "missing_coord_rate must be in [0, 1] (design §3.6)")
        _require(isinstance(self.missing_category_rate, (int, float))
                 and 0.0 <= self.missing_category_rate <= 1.0,
                 "missing_category_rate must be in [0, 1] (design §3.6)")
        _require(isinstance(self.max_candidates, int) and self.max_candidates >= 1,
                 "max_candidates must be an int >= 1 (design §3.5)")
        _require(isinstance(self.replications, int) and self.replications >= 1,
                 "replications (R) must be an int >= 1 (design §6.2)")
        _require(isinstance(self.evidence_cap, (int, float))
                 and self.evidence_cap > 0,
                 "evidence_cap (CAP) must be > 0 (design §4.4)")

    @property
    def lambda_per_day(self) -> float:
        """Arrival rate λ derived from ρ, c and E[S]: λ = ρ · c / E[S] (§3.2)."""
        return float(self.rho) * float(self.c) / float(self.mean_service_days)

    @property
    def t_end_obs_days(self) -> float:
        return float(self.t_warm_days) + float(self.t_obs_days)

    @property
    def t_cut_days(self) -> float:
        return self.t_end_obs_days + float(self.t_drain_max_days)

    def day_to_datetime(self, day_offset: float) -> datetime:
        """Map a day offset from the epoch to an absolute naive-UTC timestamp."""
        return self.epoch + timedelta(days=day_offset)

    def datetime_to_day(self, value: datetime) -> float:
        """Map an absolute naive-UTC timestamp to a day offset from the epoch."""
        return (value - self.epoch).total_seconds() / 86400.0

    def to_dict(self) -> Dict[str, Any]:
        """Config echo for reproducibility records (design §7.10).

        The ``production_constants`` block references the live production
        values by direct import (design §6.7: "referenced live, never
        retyped"), so a report can always be matched against the production
        policy that produced the evidence.
        """
        data = asdict(self)
        data["epoch"] = self.epoch.isoformat()
        data["lambda_per_day"] = self.lambda_per_day
        data["t_end_obs_days"] = self.t_end_obs_days
        data["t_cut_days"] = self.t_cut_days
        data["time_unit"] = "days"
        data["mean_service_days_status"] = (
            "documented placeholder - not calibrated to any real-world value (design §3.3)"
        )
        data["assumption_knobs"] = [
            "p_repeat", "p_high", "correlation_delta", "evidence_cap",
        ]
        data["production_constants"] = {
            "SIMILARITY_THRESHOLD": SIMILARITY_THRESHOLD,
            "LOCATION_METERS_MAX": LOCATION_METERS_MAX,
            "TIME_WINDOW_DAYS": TIME_WINDOW_DAYS,
            "REQUIRED_SIGNALS": sorted(REQUIRED_SIGNALS),
        }
        return data


@dataclass(frozen=True)
class SimComplaint:
    """One generated complaint: observable attributes + latent generator truth.

    Observable fields (design §3.6 table row 1) are shaped like the D2.1
    harness record dicts so existing production helpers (``analyze``,
    ``count_based_ranking``, ``RuleBasedPrioritizer``) can be reused
    unmodified via :meth:`to_record`. ``severity_label``/``resolved_at``
    are kept absent (None) by default (table row 2); ``created_at`` is never
    dropped (§3.6) — the engine needs an arrival time — and equals
    ``arrival``.

    Latent fields (table row 3, ``sim_``/``latent_`` prefixes):
    ``latent_issue_id`` identifies the simulated issue a complaint belongs
    to; ``sim_is_high_priority`` is the evaluation-only high-priority
    designation of §3.7 used solely by generator construction and metric
    M6. Neither may ever appear in policy-visible state (§3.1.1, §7.2).
    """

    complaint_id: str
    arrival: datetime
    service_days: float
    # Observable attributes (D3.2).
    title: str = ""
    description: str = ""
    category: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_label: Optional[str] = None
    administrative_area: Optional[str] = None
    status: str = "open"
    source: str = "simulated"
    synthetic: bool = True
    severity_label: Optional[str] = None
    resolved_at: Optional[datetime] = None
    data_quality_flags: Optional[str] = None
    # Never populated by the Phase D3 generator: inventing source-record
    # identities would fabricate import provenance (design §4.4 decision).
    raw_imported_values: Optional[str] = None
    # Latent generator truth (simulation-only, never policy-visible).
    latent_issue_id: Optional[str] = None
    sim_is_high_priority: bool = False

    def __post_init__(self) -> None:
        _require(isinstance(self.service_days, (int, float))
                 and self.service_days >= 0,
                 "service_days must be >= 0")
        if self.latitude is not None:
            _require(isinstance(self.latitude, (int, float))
                     and -90.0 <= self.latitude <= 90.0,
                     "latitude must be in [-90, 90]")
        if self.longitude is not None:
            _require(isinstance(self.longitude, (int, float))
                     and -180.0 <= self.longitude <= 180.0,
                     "longitude must be in [-180, 180]")

    def to_record(self) -> Dict[str, Any]:
        """Observable-only projection shaped like a D2.1 harness record dict.

        Used by the simulation-side adapters (``evidence.py``) and by
        parity tests against production helpers. Latent fields
        (``latent_issue_id``, ``sim_is_high_priority``) and simulation-only
        bookkeeping are deliberately excluded — a record built from this
        projection is exactly what production code is allowed to see
        (design §3.6).
        """
        return {
            "complaint_id": self.complaint_id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "location_label": self.location_label,
            "administrative_area": self.administrative_area,
            "created_at": self.arrival,
            "status": self.status,
            "source": self.source,
            "synthetic": self.synthetic,
            "severity_label": self.severity_label,
            "resolved_at": self.resolved_at,
            "data_quality_flags": self.data_quality_flags,
            "raw_imported_values": self.raw_imported_values,
        }


@dataclass(frozen=True)
class ArrivalStream:
    """Immutable generated stream, shared by every policy in a paired run.

    The stream is the common-random-numbers input of design §6.2: it is
    produced once per (config, seed) by ``generate_stream`` and is never
    mutated or re-drawn by policy execution. Complaints are ordered by
    non-decreasing arrival time with unique ids.

    ``realized_pi``, ``n_latent_issues``, ``realized_repeat_fraction`` and
    the ``realized_p_high*`` fields (D3.2) record the generator-side latent
    structure actually produced (§3.7: "the report states the realized π",
    plus empirical ``P(high)`` / ``P(high | R)`` for generated-relation
    verification). They are evaluation-side metadata and are never exposed
    to policies.
    """

    complaints: Tuple[SimComplaint, ...]
    master_seed: int
    cell_id: str
    seed: int
    realized_pi: float = 0.0
    n_latent_issues: int = 0
    # Fraction of complaints that are repeats (i.e. not the first report of
    # their latent issue); derived from the generated structure, not the
    # ``p_repeat`` coin (§3.2/§3.7 diagnostics).
    realized_repeat_fraction: float = 0.0
    # Empirical high-priority marginals for the §3.7 verification block.
    # Conditions on R are None when that stratum is empty.
    realized_p_high: float = 0.0
    realized_p_high_given_r1: Optional[float] = None
    realized_p_high_given_r0: Optional[float] = None

    def __post_init__(self) -> None:
        seen = set()
        previous = None
        for complaint in self.complaints:
            _require(complaint.complaint_id not in seen,
                     f"duplicate complaint_id in stream: {complaint.complaint_id}")
            seen.add(complaint.complaint_id)
            if previous is not None:
                _require(complaint.arrival >= previous,
                         "stream complaints must be sorted by non-decreasing arrival")
            previous = complaint.arrival

    def checksum(self) -> str:
        """Stable digest of the complete generated input (design §6.2).

        Covers every exogenous field of every complaint — arrivals, service
        times, observable attributes, and latent generator truth — plus the
        stream-level latent aggregates, so two streams are bit-identical
        exactly when their complete generated inputs are.
        """
        digest = hashlib.sha256()
        for complaint in self.complaints:
            parts = [
                complaint.complaint_id,
                complaint.arrival.isoformat(),
                repr(complaint.service_days),
                complaint.title,
                complaint.description,
                repr(complaint.category),
                repr(complaint.latitude),
                repr(complaint.longitude),
                repr(complaint.location_label),
                repr(complaint.administrative_area),
                complaint.status,
                complaint.source,
                repr(complaint.synthetic),
                repr(complaint.latent_issue_id),
                repr(complaint.sim_is_high_priority),
            ]
            digest.update("|".join(parts).encode("utf-8"))
            digest.update(b"\n")
        digest.update(
            f"realized_pi={self.realized_pi!r}|"
            f"n_latent_issues={self.n_latent_issues}|"
            f"realized_repeat_fraction={self.realized_repeat_fraction!r}|"
            f"realized_p_high={self.realized_p_high!r}|"
            f"realized_p_high_given_r1={self.realized_p_high_given_r1!r}|"
            f"realized_p_high_given_r0={self.realized_p_high_given_r0!r}\n".encode(
                "utf-8"
            )
        )
        return digest.hexdigest()


@dataclass(frozen=True)
class ComplaintOutcome:
    """Per-complaint engine record produced by ``run_policy``.

    ``start`` is ``None`` when the complaint never began service by
    ``t_cut`` (right-censored, design §5 M1/M8); ``completion`` is ``None``
    when it did not finish by ``t_cut`` (design §5 M5/M9, ``f_c^{comp}``).
    The engine never fabricates a start or completion beyond ``t_cut``.

    ``sim_is_high_priority`` is copied verbatim from the stream's latent
    field for the metrics layer's M6 subset (§5); the engine never *uses*
    it for ordering, so policies remain blind to it (§3.1.1, test §8.4.10).
    """

    complaint_id: str
    arrival: datetime
    start: Optional[datetime]
    completion: Optional[datetime]
    service_days: float
    sim_is_high_priority: bool = False


@dataclass(frozen=True)
class PolicyEvidence:
    """Frozen arrival-snapshot evidence bundle for one paired replication.

    Built once per (cell, rep) by the experiment layer from
    ``snapshot_counts`` (design §4.3, §8.3 option (c)) and
    ``arrival_snapshot_evidence`` (design §3.5, §4.4), then shared
    identically by every policy in the run (common random numbers, §6.2;
    fair-comparison checklist §6.5 items 1 and 4). Both mappings are
    complaint-id-keyed; ids absent from a mapping are treated as count 0
    (no predecessors / no relations). The engine never recomputes either
    quantity, so a policy key is fixed no later than complaint arrival
    (§3.4) and no future complaint can influence an earlier decision
    (§3.1.1).
    """

    snapshot_counts: Mapping[str, int] = field(default_factory=dict)
    d1_evidence_counts: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RunResult:
    """Immutable result of one (stream, config, policy) run.

    Outcomes follow stream order and cover every complaint in the stream,
    started or censored, so metric code can apply cohort and censoring rules
    itself without re-running the engine (design §5, §8.1 ``metrics.py``).
    """

    policy: str
    master_seed: int
    cell_id: str
    seed: int
    stopped_at: datetime
    outcomes: Tuple[ComplaintOutcome, ...]

    def outcome(self, complaint_id: str) -> ComplaintOutcome:
        for record in self.outcomes:
            if record.complaint_id == complaint_id:
                return record
        raise KeyError(complaint_id)


def observable_field_names() -> Tuple[str, ...]:
    """Field names of :class:`SimComplaint` that are observable (non-latent).

    Derived from the dataclass itself so the allowlist cannot silently
    drift from the entity: a field is observable iff its name carries no
    ``sim_``/``latent_``/``true_``/``future_``/``generator_`` prefix
    (design §3.1.1, §3.6).
    """
    forbidden_prefixes = ("sim_", "latent_", "true_", "future_", "generator_")
    return tuple(
        f.name
        for f in fields(SimComplaint)
        if not f.name.startswith(forbidden_prefixes)
    )
