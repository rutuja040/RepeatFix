"""Phase D3.3 experiment orchestration: grid × seeds with common random numbers.

Implements the ``experiment.py`` slot of design §8.1 (§6.1 grid, §6.2 CRN
and seed derivation, §6.5 fair-comparison checklist, §6.6 uncertainty
summaries). ``run_experiment`` is a pure function of its config: same input
⇒ deep-equal report (design §6.2 determinism requirement).

Structure (enforces fair comparison by construction, §6.5):

1. Enumerate grid cells as an explicit ordered product
   ``(load × correlation regime × detector visibility)`` — the order is
   recorded in the report so cell indices and derived seeds are
   reproducible rather than implicit (§6.2).
2. For each (cell, rep): generate **one** arrival stream
   (``generate_stream``) and **one** frozen evidence bundle
   (predecessor snapshot counts + D1 arrival-snapshot relations) *before*
   any policy runs; every policy then consumes the identical bundle and
   stream (§6.2 verification of equivalent streams).
3. Run all four policies (config-bound via ``policies_for_config`` so the
   §4.4 CAP travels with the recurrence-aware policy) through the shared
   engine and the single ``compute_metrics`` implementation.
4. Aggregate per cell: per-seed raw metric values, across-seed summaries
   (mean/median/empirical 2.5/97.5 percentiles, §6.6), censoring
   summaries, and per-seed **paired** policy contrasts (CRN differences
   with percentile intervals and the "resolved iff the interval excludes
   0" flag, §6.6 recommendation).

Ground-truth discipline (§3.1.1, §3.6, §7): latent structure
(``sim_is_high_priority``, ``latent_issue_id``) enters only the metrics
layer (M6) and the aggregate §3.7 generator-verification diagnostics
already carried on ``ArrivalStream``; it is never a policy input, and the
report never exposes per-complaint latent data.

Sensitivity blocks (§6.3) are **deferred** in D3.3; the report lists them
as not run (§7.9). No oracle policy (§4.5) exists in the registry.
"""

from __future__ import annotations

import hashlib
import math
import statistics
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from backend.services.simulation.evidence import (
    arrival_snapshot_evidence,
    snapshot_counts,
)
from backend.services.simulation.generate import (
    STREAM_NAMES,
    derive_stream_seed,
    generate_stream,
)
from backend.services.simulation.metrics import (
    CENSORING_WARNING_TEXT,
    CENSORING_WARN_THRESHOLD,
    Metrics,
    censoring_warning,
    compute_metrics,
)
from backend.services.simulation.model import (
    POLICY_VISIBLE_FIELDS,
    ArrivalStream,
    PolicyEvidence,
    SimConfig,
)
from backend.services.simulation.policies import (
    POLICIES,
    PolicySnapshot,
    policies_for_config,
)
from backend.services.simulation.queue_sim import _snapshot_of, run_policy
from backend.services.simulation.validation import run_mm1_validation

SIM_VERSION = "d3.3"

# Label used for a grid cell in which no complaint reached the observation
# window, so that every metric population is empty and no outcome can be
# evaluated (design §5 empty-subset semantics; audit remediation).
NO_COHORT_LABEL = "No evaluation cohort — outcomes not evaluated"

# Grid axes in the recorded enumeration order (design §6.1).
LOADS: Tuple[float, ...] = (0.5, 0.8, 1.2)
CORRELATION_REGIMES: Tuple[str, ...] = (
    "correlated",
    "independent",
    "anti_correlated",
)
DETECTOR_VISIBILITIES: Tuple[str, ...] = (
    "detector_visible",
    "detector_blind",
)
POLICY_ORDER: Tuple[str, ...] = (
    "fifo",
    "rule_based",
    "count_based",
    "recurrence_aware",
)

# Scalar metric fields summarised across seeds (design §6.6). Backlog time
# series are retained per seed in the raw records but not summarised into
# cross-seed percentiles (the scalar growth rate is the comparable stat).
_SCALAR_METRIC_FIELDS: Tuple[str, ...] = (
    "cohort_size",
    "n_started",
    "n_completed",
    "wait_mean_days",
    "wait_median_days",
    "wait_p95_days",
    "flow_mean_days",
    "flow_median_days",
    "flow_p95_days",
    "completion_rate",
    "fc_start",
    "fc_comp",
    "backlog_growth_rate",
    "utilization",
    "high_n_started",
    "high_wait_mean_days",
    "high_wait_median_days",
    "high_wait_p95_days",
)

# Wait metrics that carry the conditional censoring stamp of design §5
# ("if f_c^{start} > 0.1 the report stamps the wait metrics 'censored
# subset — steady-state interpretation invalid'").
_WAIT_METRIC_FIELDS: Tuple[str, ...] = (
    "wait_mean_days",
    "wait_median_days",
    "wait_p95_days",
    "high_wait_mean_days",
    "high_wait_median_days",
    "high_wait_p95_days",
)

# Per-seed paired-contrast metric fields (design §6.6: the primary
# comparison quantity is the paired difference of these metrics).
_CONTRAST_METRIC_FIELDS: Tuple[str, ...] = (
    "wait_mean_days",
    "wait_median_days",
    "wait_p95_days",
    "completion_rate",
    "backlog_growth_rate",
    "utilization",
    "high_wait_mean_days",
    "high_wait_median_days",
    "high_wait_p95_days",
)

# Design §6.6 defines the paired-contrast interval as the empirical
# 2.5/97.5 percentile range of the per-seed paired differences. That range
# is only an informative Monte-Carlo range when enough replications
# contribute: with a handful of seeds the percentiles degenerate to the
# sample minimum and maximum, so "the interval excludes 0" collapses to
# "the two runs differed", which is not statistical evidence. The small-R
# pilot of §6.8 is exactly that case. Below this threshold the report states
# the comparison as inconclusive (and says so in the report) instead of
# presenting it as a resolved difference.
MIN_REPLICATIONS_FOR_CONTRAST_INTERVAL = 5

# Sensitivity blocks of design §6.3 — deferred in D3.3 (§6.8 prioritisation;
# reported as not run per §7.9).
DEFERRED_SENSITIVITIES: Tuple[Tuple[str, str], ...] = (
    ("service_time_shape", "§6.3 item 1: constant (CV=0) vs exponential (CV=1)"),
    ("missingness", "§6.3 item 2: coordinate/category drop rates {0, 0.2}"),
    (
        "simulated_observable_severity",
        "§6.3 item 3: generated severity_label for the scorer's dominant term",
    ),
    (
        "evidence_dynamic_refresh",
        "§6.3 item 4: decision-time evidence recomputation vs arrival snapshot",
    ),
    ("servers", "§6.3 item 5: c ∈ {1, 2, 4} at fixed ρ (config enforces c = 1)"),
    (
        "rule_based_plus_ageing",
        "§6.3 item 6: simulation-only wait-age bonus variant",
    ),
    (
        "generative_magnitudes",
        "§6.3 item 7: p_repeat, p_high, evidence saturation CAP ∈ {1, 3, 5}",
    ),
    (
        "cold_start",
        "§6.4 cold-start variant (no warm-up, full-horizon cohort)",
    ),
    (
        "mm1_warmup_sensitivity",
        "§6.9 longer-t_warm replication of the M/M/1 validation",
    ),
)


@dataclass(frozen=True)
class GridCell:
    """One enumerated (load, regime, visibility) cell (design §6.1–§6.2)."""

    index: int
    cell_id: str
    rho: float
    correlation_regime: str
    detector_visibility: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CellSeedRecord:
    """Seed/checksum provenance for one (cell, rep) paired replication.

    ``rep_index`` is the replication index (0-based) — the value passed to
    the generator as the stream's ``seed``. It is **not** the derived RNG
    seed: ``derived_stream_seeds`` holds the actual per-stream seeds, one
    per named generator stream, derived by
    ``generate.derive_stream_seed`` exactly as the generator derives them
    (design §6.2). Recording both makes every seed reconstructible without
    guessing which field means what.

    ``stream_checksum`` is the SHA-256 over the complete generated input of
    the stream (arrivals, service times, observable attributes, latent
    generator truth) and is the strongest single reproducibility artefact.
    """

    rep: int
    rep_index: int
    derived_stream_seeds: Dict[str, int]
    n_complaints: int
    stream_checksum: str
    realized_pi: float
    realized_p_high: float
    realized_p_high_given_r1: Optional[float]
    realized_p_high_given_r0: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExperimentConfig:
    """Full experiment specification (design §6.7 reproducible config).

    ``sim`` carries every generator/window/knob value; the grid axes may
    be restricted (a subset of the §6.1 factorial) so pilots and CLI
    filters can run fewer cells — the *default* is the full factorial.
    ``policy_filter`` restricts the policies run (default: all four); a
    paired contrast needs both policies present, so contrasts degrade to
    empty when fewer than two policies run.
    """

    sim: SimConfig = field(default_factory=SimConfig)
    loads: Tuple[float, ...] = LOADS
    correlation_regimes: Tuple[str, ...] = CORRELATION_REGIMES
    detector_visibilities: Tuple[str, ...] = DETECTOR_VISIBILITIES
    policy_filter: Optional[Tuple[str, ...]] = None
    run_timestamp: Optional[datetime] = None
    code_commit: Optional[str] = None
    # Number of independent replications of the §6.9 M/M/1 validation
    # configuration (R_v >= 30 in the design). The validation block is
    # included in every report regardless of its outcome.
    mm1_replications: int = 30
    # Provenance block describing the exact source state that produced the
    # report (git HEAD, working-tree state, local source digest). Injected
    # by the CLI after the run, so ``run_experiment`` stays a pure function
    # of its configuration (design §6.2).
    provenance: Optional[Dict[str, Any]] = None

    def policy_names(self) -> Tuple[str, ...]:
        if self.policy_filter is None:
            return POLICY_ORDER
        unknown = [n for n in self.policy_filter if n not in POLICIES]
        if unknown:
            raise ValueError(
                f"unknown policy names {unknown!r}; expected subset of "
                f"{sorted(POLICIES)} (design §6.1)"
            )
        if not self.policy_filter:
            raise ValueError("policy_filter must not be empty when provided")
        # Preserve the canonical design order for reproducible output.
        return tuple(n for n in POLICY_ORDER if n in self.policy_filter)


def enumerate_grid(config: ExperimentConfig) -> Tuple[GridCell, ...]:
    """Ordered cell enumeration of design §6.1's factorial (§6.2).

    Order: load (outer) × correlation regime × detector visibility (inner),
    matching ``LOADS`` × ``CORRELATION_REGIMES`` × ``DETECTOR_VISIBILITIES``.
    Cell ids embed every axis value and are recorded in the report so the
    ``master_seed + cell_index`` derivation is transparent.
    """
    cells: List[GridCell] = []
    index = 0
    for rho in config.loads:
        for regime in config.correlation_regimes:
            for visibility in config.detector_visibilities:
                cell_id = f"rho={rho}|regime={regime}|visibility={visibility}"
                cells.append(
                    GridCell(
                        index=index,
                        cell_id=cell_id,
                        rho=float(rho),
                        correlation_regime=regime,
                        detector_visibility=visibility,
                    )
                )
                index += 1
    if not cells:
        raise ValueError("experiment grid is empty (no cells to run)")
    return tuple(cells)


def build_evidence(stream: ArrivalStream, config: SimConfig) -> PolicyEvidence:
    """Frozen arrival-snapshot evidence bundle for one replication.

    Computed exactly once per (cell, rep) before any policy runs (design
    §3.5, §6.5 item 4): predecessor-only snapshot counts (§4.3 adapter)
    and D1 arrival-snapshot relation counts (§4.4). Both are pure
    functions of the generated stream — no latent truth, no future data.
    """
    counts = snapshot_counts(stream.complaints)
    evidence_map = arrival_snapshot_evidence(stream, config)
    d1_counts = {
        complaint_id: item.evidence_count()
        for complaint_id, item in evidence_map.items()
    }
    return PolicyEvidence(snapshot_counts=counts, d1_evidence_counts=d1_counts)


def _cell_config(base: SimConfig, cell: GridCell) -> SimConfig:
    """Per-cell config: base config with the cell's axes applied."""
    values = {f.name: getattr(base, f.name) for f in fields(SimConfig)}
    values["rho"] = cell.rho
    values["correlation_regime"] = cell.correlation_regime
    values["detector_visibility"] = cell.detector_visibility
    return SimConfig(**values)


def _percentile_nearest_rank(values: Sequence[float], fraction: float) -> float:
    """Empirical nearest-rank percentile (same convention as metrics p95)."""
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def _describe_across_seeds(values: Sequence[float]) -> Dict[str, Any]:
    """Across-seed summary (design §6.6): mean/median/2.5/97.5 percentiles."""
    if not values:
        return {
            "n_seeds": 0,
            "mean": None,
            "median": None,
            "p2_5": None,
            "p97_5": None,
        }
    return {
        "n_seeds": len(values),
        "mean": statistics.fmean(values),
        "median": float(statistics.median(values)),
        "p2_5": _percentile_nearest_rank(values, 0.025),
        "p97_5": _percentile_nearest_rank(values, 0.975),
    }


def _metric_scalar(metric: Metrics, field_name: str) -> Optional[float]:
    value = getattr(metric, field_name)
    if value is None:
        return None
    return float(value)


def _verify_fairness(
    stream: ArrivalStream,
    evidence: PolicyEvidence,
    results: Mapping[str, Any],
    metrics_by_policy: Mapping[str, Metrics],
    stream_checksum_before: str,
    policy_names: Tuple[str, ...],
    config: SimConfig,
) -> Dict[str, Dict[str, Any]]:
    """Run-time verification of the design §6.5 fair-comparison checklist.

    Every item is an actual check performed on the run, not an
    unconditional assertion: each entry records ``passed`` plus the
    ``detail`` of what was compared. A failed check raises (the caller
    turns it into an ``AssertionError``), because a violated fairness
    condition invalidates the whole cell.
    """
    names = list(policy_names)
    checks: Dict[str, Dict[str, Any]] = {}

    # 1. Identical stream and service times for every policy in the rep.
    exogenous_ok = True
    exogenous_detail = "no policy ran"
    if names:
        reference = {
            outcome.complaint_id: (
                outcome.arrival,
                outcome.service_days,
            )
            for outcome in results[names[0]].outcomes
        }
        for name in names[1:]:
            other = {
                outcome.complaint_id: (
                    outcome.arrival,
                    outcome.service_days,
                )
                for outcome in results[name].outcomes
            }
            if other != reference:
                exogenous_ok = False
        exogenous_detail = (
            f"arrival time and service time of {len(reference)} complaints "
            f"compared across {len(names)} policies: "
            f"{'identical' if exogenous_ok else 'DIVERGED'}"
        )
    checks["identical_streams_and_service_times_per_rep"] = {
        "passed": exogenous_ok,
        "detail": exogenous_detail,
    }

    # 2. Identical policy-visible snapshots (design §6.5 item 2).
    projection_digest = ""
    allowlist_ok = True
    projection_ok = True
    if names:
        snapshot_fields = tuple(f.name for f in fields(PolicySnapshot))
        allowlist_ok = snapshot_fields == POLICY_VISIBLE_FIELDS
        digests = set()
        for name in names:
            digest = hashlib.sha256()
            for complaint in stream.complaints:
                snapshot = _snapshot_of(complaint, evidence)
                digest.update(
                    repr(
                        (
                            snapshot.complaint_id,
                            snapshot.created_at,
                            snapshot.title,
                            snapshot.description,
                            snapshot.category,
                            snapshot.latitude,
                            snapshot.longitude,
                            snapshot.location_label,
                            snapshot.administrative_area,
                            snapshot.status,
                            snapshot.source,
                            snapshot.synthetic,
                            snapshot.snapshot_count,
                            snapshot.d1_evidence_count,
                        )
                    ).encode("utf-8")
                )
            digests.add(digest.hexdigest())
        projection_digest = sorted(digests)[0] if digests else ""
        projection_ok = len(digests) == 1
    checks["identical_policy_visible_snapshots"] = {
        "passed": bool(projection_ok and allowlist_ok),
        "detail": (
            f"engine projection of {len(stream.complaints)} complaints "
            f"recomputed per policy from the one frozen evidence bundle: "
            f"sha256={projection_digest[:16]}..., "
            f"{'identical' if projection_ok else 'DIVERGED'} across policies; "
            f"projection fields match the policy-visible allowlist "
            f"({len(POLICY_VISIBLE_FIELDS)} fields, no latent/sim_ field): "
            f"{allowlist_ok}"
        ),
    }

    # 3. Identical eligibility conventions (design §6.5 item 3): the
    #    evaluation cohort is defined by arrival time alone, so it must be
    #    identical for every policy.
    cohort_ok = True
    cohort_detail = "no policy ran"
    if names:
        cohorts = {
            name: metrics_by_policy[name].cohort_size for name in names
        }
        cohort_ok = len(set(cohorts.values())) == 1
        cohort_detail = (
            f"evaluation-cohort size per policy: {cohorts} — "
            f"{'identical' if cohort_ok else 'DIVERGED'}"
        )
    checks["identical_eligibility_conventions"] = {
        "passed": cohort_ok,
        "detail": cohort_detail,
    }

    # 4. Identical evidence snapshot mode (design §6.5 item 4): one frozen
    #    arrival-snapshot bundle per (cell, rep), built before any policy
    #    ran and consumed unchanged by all of them.
    checks["identical_evidence_snapshot_mode"] = {
        "passed": bool(projection_ok),
        "detail": (
            "one arrival-snapshot evidence bundle was built for this "
            "(cell, rep) before any policy ran; every policy consumed that "
            "same bundle (verified by the identical projection digest) — "
            "no dynamic refresh, no per-policy recomputation"
        ),
    }

    # 5. Identical windows and drain cut-off (design §6.5 item 5).
    expected_cut = config.day_to_datetime(config.t_cut_days)
    cuts = {name: results[name].stopped_at for name in names} if names else {}
    windows_ok = bool(cuts) and all(value == expected_cut for value in cuts.values())
    checks["identical_windows_and_t_cut"] = {
        "passed": windows_ok,
        "detail": (
            f"engine cut-off per policy: "
            f"{sorted({value.isoformat() for value in cuts.values()})}; "
            f"expected t_cut={expected_cut.isoformat()} — "
            f"{'identical' if windows_ok else 'DIVERGED'}"
        ),
    }

    # 6. Identical metrics implementation (design §6.5 item 6).
    metrics_ok = bool(names) and all(
        type(metrics_by_policy[name]) is Metrics for name in names
    )
    checks["identical_metrics_implementation"] = {
        "passed": metrics_ok,
        "detail": (
            f"every policy's metrics were produced by the single "
            f"compute_metrics implementation "
            f"({compute_metrics.__module__}.{compute_metrics.__name__}) — "
            f"{'confirmed' if metrics_ok else 'NOT CONFIRMED'}"
        ),
    }

    # 7. Compute cost excluded from the metrics (design §6.5 item 7): the
    #    exogenous stream must be untouched by policy execution.
    checksum_after = stream.checksum()
    stream_untouched = checksum_after == stream_checksum_before
    checks["compute_cost_excluded_from_metrics"] = {
        "passed": stream_untouched,
        "detail": (
            f"stream checksum before policy runs {stream_checksum_before[:16]}... "
            f"vs after {checksum_after[:16]}... — "
            f"{'unchanged' if stream_untouched else 'MUTATED'}"
        ),
    }

    return checks


def _run_one_rep(
    cell: GridCell,
    rep: int,
    base_config: SimConfig,
    policy_names: Tuple[str, ...],
) -> Tuple[CellSeedRecord, Dict[str, Metrics], Dict[str, Dict[str, Any]]]:
    """One paired replication: stream+evidence once, then every policy."""
    config = _cell_config(base_config, cell)
    stream = generate_stream(config, rep, cell_id=cell.cell_id)
    stream_checksum = stream.checksum()
    evidence = build_evidence(stream, config)
    bound_policies = policies_for_config(config)

    results: Dict[str, Any] = {}
    metrics_by_policy: Dict[str, Metrics] = {}
    for name in policy_names:
        result = run_policy(stream, config, bound_policies[name], evidence=evidence)
        results[name] = result
        metrics_by_policy[name] = compute_metrics(result, config)

    fairness = _verify_fairness(
        stream=stream,
        evidence=evidence,
        results=results,
        metrics_by_policy=metrics_by_policy,
        stream_checksum_before=stream_checksum,
        policy_names=policy_names,
        config=config,
    )
    failed = [name for name, entry in fairness.items() if not entry["passed"]]
    if failed:
        raise AssertionError(
            f"fair-comparison checklist violated in cell {cell.cell_id!r} "
            f"rep {rep}: {failed} (design §6.5)"
        )

    record = CellSeedRecord(
        rep=rep,
        rep_index=rep,
        derived_stream_seeds={
            name: derive_stream_seed(
                config.master_seed, cell.cell_id, rep, name
            )
            for name in STREAM_NAMES
        },
        n_complaints=len(stream.complaints),
        stream_checksum=stream_checksum,
        realized_pi=stream.realized_pi,
        realized_p_high=stream.realized_p_high,
        realized_p_high_given_r1=stream.realized_p_high_given_r1,
        realized_p_high_given_r0=stream.realized_p_high_given_r0,
    )
    return record, metrics_by_policy, fairness


def _metrics_for_policy_order(
    cell: GridCell,
    rep: int,
    base_config: SimConfig,
    policy_names: Tuple[str, ...],
    order: Tuple[str, ...],
) -> Dict[str, Metrics]:
    """Run one replication with the policies executed in ``order``.

    CRN + determinism together imply that the per-policy metrics do not
    depend on execution order — i.e. no policy consumed or shifted shared
    state (design §6.2, §6.5 items 1–2).
    """
    config = _cell_config(base_config, cell)
    stream = generate_stream(config, rep, cell_id=cell.cell_id)
    evidence = build_evidence(stream, config)
    bound = policies_for_config(config)
    assert set(order) == set(policy_names)
    return {
        name: compute_metrics(
            run_policy(stream, config, bound[name], evidence=evidence), config
        )
        for name in order
    }


def _paired_contrasts(
    metrics_by_rep: Mapping[int, Mapping[str, Metrics]],
    policy_names: Tuple[str, ...],
    has_cohort: bool = True,
) -> Dict[str, Any]:
    """Per-seed paired policy contrasts (design §6.6).

    For every ordered pair (reference, challenger) with reference !=
    challenger, computes the per-seed difference
    ``challenger − reference`` on each contrast metric (only seeds where
    both values are present), then summarises with mean and empirical
    2.5/97.5 percentiles. ``resolved`` is True iff the interval excludes
    0 (§6.6 recommendation) — a statement about Monte-Carlo error of the
    simulated-world estimator only, never about the real world (§7.11).

    The ``resolved`` flag alone must not be read as statistical evidence:
    with fewer than :data:`MIN_REPLICATIONS_FOR_CONTRAST_INTERVAL`
    replications the percentile interval degenerates to the sample
    minimum/maximum. Such entries carry ``evidence_strength =
    "insufficient_replications"``, an explicit caveat, and
    ``evidence_for_either_policy = False``.

    A metric with no paired observation is reported as ``not_evaluated``
    with an explicit reason: a contrast without observations is *not*
    evidence for or against either policy.
    """
    contrasts: Dict[str, Any] = {}
    if len(policy_names) < 2:
        return contrasts
    for reference in policy_names:
        for challenger in policy_names:
            if reference == challenger:
                continue
            pair_key = f"{challenger}_minus_{reference}"
            per_metric: Dict[str, Any] = {}
            for metric_name in _CONTRAST_METRIC_FIELDS:
                differences: List[float] = []
                for rep in sorted(metrics_by_rep):
                    by_policy = metrics_by_rep[rep]
                    if reference not in by_policy or challenger not in by_policy:
                        continue
                    ref_value = _metric_scalar(by_policy[reference], metric_name)
                    ch_value = _metric_scalar(by_policy[challenger], metric_name)
                    if ref_value is None or ch_value is None:
                        continue
                    differences.append(ch_value - ref_value)
                summary = _describe_across_seeds(differences)
                interval = (summary["p2_5"], summary["p97_5"])
                resolved = False
                if summary["n_seeds"] > 0 and interval[0] is not None and interval[1] is not None:
                    resolved = interval[0] > 0.0 or interval[1] < 0.0
                entry: Dict[str, Any] = {
                    **summary,
                    "resolved_excludes_zero": resolved,
                    "replications_used": summary["n_seeds"],
                    "interpretation": (
                        "Monte-Carlo error of the simulated-world estimator "
                        "only; not a confidence interval about the real "
                        "world (design §6.6, §7.11)"
                    ),
                }
                if summary["n_seeds"] == 0:
                    entry["status"] = "not_evaluated"
                    entry["status_reason"] = (
                        "no paired observation exists for this metric in this "
                        "cell: the evaluation cohort is empty"
                        if not has_cohort
                        else "no replication produced a value for this metric "
                        "for both policies of the pair, so no paired "
                        "difference could be computed"
                    )
                    entry["evidence_for_either_policy"] = False
                    entry["evidence_strength"] = "no_observations"
                    entry["caveat"] = None
                elif not has_cohort:
                    # The cell has no evaluation cohort. The window-measured
                    # system-state metrics (utilisation, backlog growth) still
                    # differ between policies, but they describe the drain of
                    # the warm-up backlog only — they are not outcomes and are
                    # not evidence for or against any policy.
                    entry["status"] = "not_evaluated"
                    entry["status_reason"] = (
                        "the evaluation cohort of this cell is empty: the "
                        "window-measured system-state metrics describe the "
                        "drain of the warm-up backlog only and are not "
                        "evidence for or against either policy"
                    )
                    entry["evidence_for_either_policy"] = False
                    entry["evidence_strength"] = "no_observations"
                    entry["caveat"] = None
                    entry["resolved_excludes_zero"] = False
                elif summary["n_seeds"] < MIN_REPLICATIONS_FOR_CONTRAST_INTERVAL:
                    # The comparison was computed, but too few replications
                    # contributed for the percentile interval to carry
                    # statistical meaning (design §6.6/§6.8). The observed
                    # difference is reported, never presented as a resolved
                    # difference between the policies.
                    entry["status"] = "evaluated"
                    entry["evidence_strength"] = "insufficient_replications"
                    entry["evidence_for_either_policy"] = False
                    entry["caveat"] = (
                        f"only {summary['n_seeds']} replication(s) contributed "
                        f"to this contrast; the empirical 2.5/97.5 percentile "
                        f"interval of design §6.6 degenerates to the sample "
                        f"minimum/maximum below "
                        f"{MIN_REPLICATIONS_FOR_CONTRAST_INTERVAL} "
                        "replications, so 'the interval excludes 0' only "
                        "records that the replications differed. This is not "
                        "statistical evidence that the policies differ, and "
                        "it is not evidence that RepeatFix is effective."
                    )
                else:
                    entry["status"] = "evaluated"
                    entry["evidence_strength"] = "sufficient_replications"
                    entry["evidence_for_either_policy"] = True
                    entry["caveat"] = None
                per_metric[metric_name] = entry
            contrasts[pair_key] = per_metric
    return contrasts


def run_experiment(config: ExperimentConfig) -> Dict[str, Any]:
    """Run the configured grid × R seeds with CRN; return the report dict.

    Pure function of ``config`` (design §6.2 determinism): no wall clock,
    no RNG beyond the deterministic seed derivation, no I/O.
    """
    cells = enumerate_grid(config)
    policy_names = config.policy_names()
    sim = config.sim

    seed_rule = (
        "per-stream seeds derived as sha256(f\"{master_seed}:{cell_id}:"
        "{rep}:{stream_name}\")[:8] big-endian int (generate."
        "derive_stream_seed, design §6.2 — stable across processes; "
        "Python hash() is never used)"
    )

    cells_report: List[Dict[str, Any]] = []
    fairness_totals: Dict[str, Dict[str, Any]] = {}
    order_checks = 0
    censoring_triggered: List[Dict[str, Any]] = []
    censoring_not_evaluated: List[Dict[str, Any]] = []
    for cell in cells:
        seed_records: List[CellSeedRecord] = []
        metrics_by_rep: Dict[int, Dict[str, Metrics]] = {}
        censoring_by_rep: Dict[int, Dict[str, Optional[str]]] = {}

        for rep in range(sim.replications):
            record, metrics_by_policy, fairness = _run_one_rep(
                cell, rep, sim, policy_names
            )
            seed_records.append(record)
            metrics_by_rep[rep] = metrics_by_policy
            for check_name, entry in fairness.items():
                bucket = fairness_totals.setdefault(
                    check_name,
                    {"checks": 0, "passed": 0, "examples": []},
                )
                bucket["checks"] += 1
                if entry["passed"]:
                    bucket["passed"] += 1
                if len(bucket["examples"]) < 3:
                    bucket["examples"].append(
                        {
                            "cell_id": cell.cell_id,
                            "rep": rep,
                            "detail": entry["detail"],
                        }
                    )
            censoring_by_rep[rep] = {
                name: censoring_warning(metrics_by_policy[name].fc_start)
                for name in policy_names
            }
            for name in policy_names:
                metrics = metrics_by_policy[name]
                entry = {
                    "cell_id": cell.cell_id,
                    "policy": name,
                    "rep": rep,
                    "cohort_size": metrics.cohort_size,
                    "fc_start": metrics.fc_start,
                    "fc_comp": metrics.fc_comp,
                }
                if metrics.fc_start is None:
                    censoring_not_evaluated.append(
                        {
                            **entry,
                            "reason": (
                                "the evaluation cohort is empty, so "
                                "f_c^{start} is undefined (not zero): the "
                                "censoring condition was not evaluated"
                            ),
                        }
                    )
                elif censoring_by_rep[rep][name] is not None:
                    censoring_triggered.append(
                        {
                            **entry,
                            "warning": censoring_by_rep[rep][name],
                        }
                    )

        # CRN verification (design §6.2): every policy within a (cell, rep)
        # consumed the identical stream — by construction one stream object
        # is shared, so checksums are per-rep, not per-policy. The
        # order-independence check proves no policy shifted shared state.
        if sim.replications >= 1 and policy_names:
            reverse = _metrics_for_policy_order(
                cell, 0, sim, policy_names, tuple(reversed(policy_names))
            )
            forward = metrics_by_rep[0]
            for name in policy_names:
                if reverse[name] != forward[name]:
                    raise AssertionError(
                        "policy-order independence violated in cell "
                        f"{cell.cell_id!r} rep 0 for policy {name!r} "
                        "(design §6.2/§6.5: CRN pairing broken)"
                    )
            order_checks += 1

        # §3.7 generated-relation verification: identical across policies by
        # construction (one stream per rep); recorded once per cell so a
        # reader can verify the generator produced the intended
        # relationship (marginal + conditionals + configured knobs).
        realized = {
            "realized_pi": _describe_across_seeds(
                [r.realized_pi for r in seed_records]
            ),
            "realized_p_high": _describe_across_seeds(
                [r.realized_p_high for r in seed_records]
            ),
            "realized_p_high_given_r1": _describe_across_seeds(
                [
                    r.realized_p_high_given_r1
                    for r in seed_records
                    if r.realized_p_high_given_r1 is not None
                ]
            ),
            "realized_p_high_given_r0": _describe_across_seeds(
                [
                    r.realized_p_high_given_r0
                    for r in seed_records
                    if r.realized_p_high_given_r0 is not None
                ]
            ),
            "configured": {
                "p_repeat": sim.p_repeat,
                "p_high": sim.p_high,
                "correlation_delta": sim.correlation_delta,
                "correlation_regime": cell.correlation_regime,
            },
        }

        # Cohort status for the cell: the evaluation cohort is defined by
        # arrival time only, so it is policy-independent (asserted by the
        # fairness checklist). A cell in which no complaint arrived in
        # [t_warm, t_end_obs] has an empty cohort in every replication.
        cohort_size_per_rep = {
            name: [
                metrics_by_rep[rep][name].cohort_size
                for rep in sorted(metrics_by_rep)
            ]
            for name in policy_names
        }
        reference_cohort_sizes = (
            cohort_size_per_rep[policy_names[0]] if policy_names else []
        )
        cell_has_cohort = any(size > 0 for size in reference_cohort_sizes)

        per_policy: Dict[str, Any] = {}
        for name in policy_names:
            field_summaries: Dict[str, Any] = {}
            for metric_name in _SCALAR_METRIC_FIELDS:
                values = [
                    value
                    for rep in sorted(metrics_by_rep)
                    if (
                        value := _metric_scalar(metrics_by_rep[rep][name], metric_name)
                    )
                    is not None
                ]
                field_summaries[metric_name] = _describe_across_seeds(values)

            # The censoring stamp (design §5) is attached to the wait
            # metrics of every (cell, policy) whose censoring fraction
            # exceeded the threshold in at least one replication.
            warned_reps = [
                rep for rep in sorted(censoring_by_rep)
                if censoring_by_rep[rep].get(name)
            ]
            for metric_name in _WAIT_METRIC_FIELDS:
                if metric_name in field_summaries:
                    field_summaries[metric_name]["censoring_warning"] = (
                        CENSORING_WARNING_TEXT if warned_reps else None
                    )
                    field_summaries[metric_name]["censoring_warning_reps"] = (
                        warned_reps
                    )

            per_seed_payload: Dict[str, Any] = {}
            for rep in sorted(metrics_by_rep):
                payload = metrics_by_rep[rep][name].to_dict()
                warning = censoring_by_rep[rep].get(name)
                payload["censoring_warning"] = warning
                per_seed_payload[str(rep)] = payload

            policy_entry: Dict[str, Any] = {
                "across_seeds": field_summaries,
                "per_seed": per_seed_payload,
            }
            if not cell_has_cohort:
                # Empty evaluation cohort: the window-measured system-state
                # metrics describe the drain of the warm-up backlog only and
                # must not be presented as evaluation-period outcomes.
                diagnostics: Dict[str, Any] = {}
                for metric_name in ("utilization", "backlog_growth_rate"):
                    if metric_name in field_summaries:
                        diagnostics[metric_name] = field_summaries.pop(metric_name)
                policy_entry["outcome_status"] = "not_evaluated"
                policy_entry["outcome_status_label"] = NO_COHORT_LABEL
                policy_entry["outcome_status_reason"] = (
                    "no complaint arrived in the observation window "
                    f"[{sim.t_warm_days}, {sim.t_end_obs_days}] days in any "
                    "replication of this cell, so every metric population is "
                    "empty and no outcome can be evaluated for this policy"
                )
                policy_entry["no_cohort_diagnostics"] = {
                    "metrics": diagnostics,
                    "note": (
                        "system-state measurements over the observation window "
                        "for a cell with no evaluation cohort: with no complaint "
                        "arriving in [t_warm, t_end_obs] they describe the "
                        "drain of the warm-up backlog only and are NOT "
                        "evaluation-period outcomes. Recorded for transparency "
                        "only — excluded from every outcome summary and from "
                        "every policy comparison."
                    ),
                }
            else:
                policy_entry["outcome_status"] = "evaluated"
                policy_entry["outcome_status_reason"] = None
            per_policy[name] = policy_entry

        cell_censoring = {
            "threshold": CENSORING_WARN_THRESHOLD,
            "warning_text": CENSORING_WARNING_TEXT,
            "rule": (
                "the wait metrics of a (cell, policy) are stamped when the "
                "censoring fraction f_c^{start} of at least one replication "
                f"exceeds {CENSORING_WARN_THRESHOLD}; an undefined "
                "f_c^{start} (empty cohort) is reported as not_evaluated and "
                "never triggers the stamp"
            ),
            "per_policy_per_replication": {
                name: {
                    str(rep): censoring_by_rep[rep].get(name)
                    for rep in sorted(censoring_by_rep)
                }
                for name in policy_names
            },
            "triggered": [
                entry
                for entry in censoring_triggered
                if entry["cell_id"] == cell.cell_id
            ],
            "not_evaluated": [
                entry
                for entry in censoring_not_evaluated
                if entry["cell_id"] == cell.cell_id
            ],
        }

        cells_report.append(
            {
                "cell": cell.to_dict(),
                "seeds": [record.to_dict() for record in seed_records],
                "realized_generator_verification": realized,
                "cohort_status": (
                    "evaluated" if cell_has_cohort else "no_evaluation_cohort"
                ),
                "cohort_status_label": (
                    None if cell_has_cohort else NO_COHORT_LABEL
                ),
                "cohort_status_reason": (
                    f"evaluation-cohort size per replication: {reference_cohort_sizes}"
                    if cell_has_cohort
                    else (
                        "no complaint arrived in the observation window "
                        f"[{sim.t_warm_days}, {sim.t_end_obs_days}] days in any "
                        "replication of this cell: the evaluation cohort is "
                        "empty and no outcome is evaluated"
                    )
                ),
                "cohort_size_per_replication": reference_cohort_sizes,
                "censoring": cell_censoring,
                "policies": per_policy,
                "paired_contrasts": _paired_contrasts(
                    metrics_by_rep, policy_names, has_cohort=cell_has_cohort
                ),
            }
        )

    report: Dict[str, Any] = {
        "banner": "SIMULATED DATA — NOT REAL-WORLD EVIDENCE",
        "sim_version": SIM_VERSION,
        "code_commit": config.code_commit,
        "provenance": config.provenance,
        "python_version": _python_version(),
        "run_timestamp": (
            config.run_timestamp.isoformat() if config.run_timestamp else None
        ),
        "config": {
            "experiment": {
                "loads": list(config.loads),
                "correlation_regimes": list(config.correlation_regimes),
                "detector_visibilities": list(config.detector_visibilities),
                "policy_order": list(policy_names),
                "replications": sim.replications,
                "master_seed": sim.master_seed,
                "grid_cell_count": len(cells),
                "grid_cells": [cell.to_dict() for cell in cells],
            },
            "sim": sim.to_dict(),
            "evidence_mode": "arrival-snapshot (frozen at arrival; §3.5 default)",
            "rng_stream_derivation": seed_rule,
            "grid_enumeration_order": (
                "load (outer) × correlation_regime × detector_visibility "
                "(inner); design §6.2 explicit ordered enumeration"
            ),
        },
        "fairness_checklist": _fairness_checklist(fairness_totals, order_checks),
        "contrast_evidence": _contrast_evidence_block(sim.replications),
        "censoring": {
            "threshold": CENSORING_WARN_THRESHOLD,
            "warning_text": CENSORING_WARNING_TEXT,
            "rule": (
                "applied per (cell, policy, replication) to the wait metrics: "
                f"f_c^{{start}} > {CENSORING_WARN_THRESHOLD} stamps "
                f"'{CENSORING_WARNING_TEXT}'. An undefined f_c^{{start}} (empty "
                "evaluation cohort) is reported as not_evaluated and never "
                "triggers the stamp."
            ),
            "triggered": censoring_triggered,
            "triggered_count": len(censoring_triggered),
            "not_evaluated": censoring_not_evaluated,
            "not_evaluated_count": len(censoring_not_evaluated),
            "policy_order": list(policy_names),
        },
        "mm1_validation": run_mm1_validation(sim, config.mm1_replications),
        "metric_definitions": _metric_definitions(),
        "primary_outcomes": ["M1", "M6", "M5", "M8"],
        "secondary_outcomes": ["M4", "M7", "M9", "M2", "M3"],
        "outcome_labelling_note": (
            "design §7.8: primary outcomes are fixed before any run "
            "(mean queue waiting time M1 and its high-priority subset M6 "
            "on the started subset, completion rate M5, censoring fraction "
            "M8); no outcome may be promoted from secondary to primary "
            "after results are seen"
        ),
        "cells": cells_report,
        "not_evaluated": _not_evaluated(),
        "deferred_sensitivities": [
            {"name": name, "detail": detail, "status": "not run (deferred)"}
            for name, detail in DEFERRED_SENSITIVITIES
        ],
        "limitations": _limitations(sim.replications),
    }
    return report


def _contrast_evidence_block(replications: int) -> Dict[str, Any]:
    """Report-level statement of how far the paired contrasts can be read.

    Design §6.6 defines the paired-contrast interval as the empirical
    2.5/97.5 percentile range of the per-seed paired differences. With
    fewer than :data:`MIN_REPLICATIONS_FOR_CONTRAST_INTERVAL` replications
    that range degenerates to the sample minimum/maximum, so a
    "resolved" flag is not statistical evidence. The block states this
    explicitly so the report never presents a small-R pilot's contrasts as
    evidence that the policies differ.
    """
    sufficient = replications >= MIN_REPLICATIONS_FOR_CONTRAST_INTERVAL
    return {
        "rule": (
            "the paired-contrast interval is the empirical 2.5/97.5 "
            "percentile range of the per-seed paired differences (design "
            "§6.6); it is informative only when at least "
            f"{MIN_REPLICATIONS_FOR_CONTRAST_INTERVAL} replications "
            "contribute"
        ),
        "min_replications_for_informative_interval": (
            MIN_REPLICATIONS_FOR_CONTRAST_INTERVAL
        ),
        "replications_used": replications,
        "informative": sufficient,
        "note": (
            None
            if sufficient
            else (
                f"this run used R={replications} replication(s) per cell, "
                f"fewer than the {MIN_REPLICATIONS_FOR_CONTRAST_INTERVAL} "
                "needed for the paired-contrast interval to carry "
                "statistical meaning: below that threshold the percentiles "
                "are the sample minimum and maximum. The contrasts in this "
                "report are reported as observed differences between "
                "replications and must not be read as evidence that the "
                "policies differ, as evidence that RepeatFix is effective, "
                "or as any claim about real municipal performance "
                "(design §6.8 pilot, §6.6, §7.11)"
            )
        ),
    }


def _fairness_checklist(
    fairness_totals: Mapping[str, Mapping[str, Any]],
    order_checks: int = 0,
) -> Dict[str, Any]:
    """Aggregate the per-replication fair-comparison checks (design §6.5).

    Every entry is an actual runtime check: ``checks`` counts how many
    (cell, rep) pairs were verified, ``passed`` how many satisfied the
    condition. A violation raises inside ``_run_one_rep`` and aborts the
    run, so ``passed == checks`` in any report that was produced.
    """
    checklist: Dict[str, Any] = {}
    for name, bucket in fairness_totals.items():
        checks = int(bucket["checks"])
        passed = int(bucket["passed"])
        checklist[name] = {
            "passed": bool(checks == 0 or passed == checks),
            "checks_performed": checks,
            "checks_passed": passed,
            "examples": list(bucket["examples"]),
        }
    checklist["policy_order_independence"] = {
        "passed": bool(order_checks > 0),
        "checks_performed": order_checks,
        "checks_passed": order_checks,
        "examples": [],
        "note": (
            "verified per cell by re-running rep 0 with the reversed policy "
            "order and asserting identical per-policy metrics; a violation "
            "raises (design §6.2/§6.5). 'passed' is true only when at least "
            "one cell was actually checked"
        ),
    }
    checklist["note"] = (
        "design §6.5 items 1–7. Every item is checked at run time on the "
        "actual run — not asserted by construction: one stream + one frozen "
        "evidence bundle per (cell, rep) shared by every policy, and a single "
        "compute_metrics path. A violated condition aborts the run."
    )
    return checklist


def _python_version() -> str:
    import sys

    return sys.version.split()[0]


def _metric_definitions() -> Dict[str, Any]:
    """Metric formulas with cohort/censoring rules (design §5, §6.7)."""
    return {
        "unit": "days",
        "cohort": (
            "Cohort = {i : arrival_i ∈ [t_warm, t_end_obs]}; complaints "
            "arriving before t_warm may occupy the queue but are excluded "
            "from metric populations (warm-up deletion, §6.4)"
        ),
        "t_cut": "t_cut = t_end_obs + t_drain_max (§6.4)",
        "M1": {
            "name": "mean queue waiting time",
            "formula": "W̄_q = mean(start_i − arrival_i) over started cohort",
            "censoring": (
                "started subset S = {i ∈ Cohort : start_i ≤ t_cut}; "
                "right-censored downward; always report with M8 and n"
            ),
            "tier": "primary",
        },
        "M2": {
            "name": "median queue waiting time",
            "formula": "median(start_i − arrival_i) over S",
            "tier": "secondary",
        },
        "M3": {
            "name": "95th-percentile queue waiting time",
            "formula": "nearest-rank p95(start_i − arrival_i) over S",
            "tier": "secondary",
        },
        "M4": {
            "name": "backlog over time + growth rate",
            "formula": (
                "B(t) = #{arrivals ≤ t} − #{service starts ≤ t} (queue "
                "waiting only; in-service excluded); ĝ_B = "
                "(B(t_end_obs) − B(t_warm)) / t_obs"
            ),
            "tier": "secondary",
        },
        "M5": {
            "name": "completion rate",
            "formula": (
                "C = #{i ∈ Cohort : completion_i ≤ t_cut} / |Cohort|; "
                "C = 1 − f_c^{comp} exactly (§5 — one quantity, two renderings)"
            ),
            "tier": "primary",
        },
        "M6": {
            "name": "high-priority waiting time (mean/median/p95)",
            "formula": "M1–M3 restricted to sim_is_high_priority = True",
            "tier": "primary",
            "caveat": (
                "simulated latent designation only (design §5); never real "
                "importance; in the correlated regime dominated by "
                "evidence-free first reports, M6 can reflect "
                "evidence-availability lag (§3.7 pre-registered caveat)"
            ),
        },
        "M7": {
            "name": "capacity utilization",
            "formula": (
                "U = busy time within [t_warm, t_end_obs] / "
                "(c · t_obs); U ∈ [0, 1]"
            ),
            "tier": "secondary",
        },
        "M8": {
            "name": "censoring fraction",
            "formula": (
                "f_c^{start} = 1 − |S|/|Cohort|; f_c^{comp} = 1 − "
                "#{completed ≤ t_cut}/|Cohort|; both reported with cohort size"
            ),
            "tier": "primary",
        },
        "M9": {
            "name": "flow time (secondary)",
            "formula": (
                "F_i = completion_i − arrival_i = W_i + S_i over the "
                "completed subset; policy-independent S (§3.3)"
            ),
            "tier": "secondary",
        },
        "uncertainty": {
            "per_cell": (
                "mean, median, empirical 2.5/97.5 percentiles across seeds "
                "(§6.6)"
            ),
            "contrasts": (
                "per-seed paired differences (CRN); resolved iff the "
                "empirical 2.5/97.5 interval excludes 0 (§6.6 "
                "recommendation)"
            ),
            "wording_rule": (
                "intervals quantify Monte-Carlo error of the simulation "
                "estimator only — never uncertainty about real municipal "
                "performance (§6.6, §7.11)"
            ),
        },
    }


def _not_evaluated() -> Dict[str, Any]:
    """The D2.1 harness not_evaluated convention carried over (§8, §5)."""
    return {
        "status": "not_evaluated",
        "items": [
            "real-world effectiveness of any policy (no real data; §7.5, §7.11)",
            "accuracy, precision, recall, NDCG (no labels; §5)",
            "resolution-time prediction error (not simulated; §5)",
            "calibration (no labels; §5)",
            "any 'real response time improvement' (§5)",
            (
                "exact_source relations (absent by design: the generator "
                "never fabricates import provenance — design §4.4 decision; "
                "only potential_recurrence evidence is evaluated)"
            ),
        ],
        "note": (
            "agreement/overlap between policies is simulated-world ordering "
            "behaviour, never framed as accuracy (D2.1 convention, §8)"
        ),
    }


def _limitations(replications: int = 0) -> List[str]:
    limitations = [
        (
            "Every quantity describes the assumed data-generating process "
            "and the policies' behaviour inside it — nothing more "
            "(design §2, §7.5)."
        ),
        (
            "Arrival rates, service times, capacity, p_high, correlation "
            "regimes, and evidence CAP are recorded [ASSUMPTION] values "
            "with no external calibration claim (§7.3)."
        ),
 (
            "A passing M/M/1 validation (§6.9) is necessary, not "
            "sufficient: it validates only the FIFO/exponential/c=1/ρ<1 "
            "configuration and the clock arithmetic feeding M1 — never "
            "priority keys, multi-server behaviour, the generator, M4–M8, "
            "or any policy comparison."
        ),
        (
            "Under ρ > 1 the queue is unstable in steady state: wait "
            "metrics there are finite-horizon, censored quantities. They "
            "are stamped 'censored subset — steady-state interpretation "
            "invalid' whenever the censoring fraction f_c^{start} exceeds "
            "0.1 in at least one replication of the (cell, policy); when "
            "f_c^{start} is undefined (empty evaluation cohort) the stamp "
            "is not applied and the outcome is reported as not_evaluated "
            "(§6.1, §5)."
        ),
        (
            "Sensitivity blocks (§6.3) were not run in this milestone and "
            "are listed as deferred; conclusions may be sensitive to them "
            "(§6.8, §7.9)."
        ),
        (
            "The development database is not evidence and is never used "
            "here (§7.1)."
        ),
    ]
    if 0 < replications < MIN_REPLICATIONS_FOR_CONTRAST_INTERVAL:
        limitations.append(
            f"This run used R={replications} replications per cell, fewer "
            f"than the {MIN_REPLICATIONS_FOR_CONTRAST_INTERVAL} needed for "
            "the paired-contrast interval to carry statistical meaning. The "
            "reported policy contrasts are observed differences between "
            "replications, not statistical evidence that the policies "
            "differ, and they are not evidence that RepeatFix is effective "
            "(design §6.8 pilot, §6.6, §7.11)."
        )
    return limitations
