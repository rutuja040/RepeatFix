"""Phase D3.1 queue metrics (design §5), one implementation for all policies.

``compute_metrics`` derives every metric from an immutable
:class:`~backend.services.simulation.model.RunResult` plus the window
configuration — it never re-runs the engine and never reads generator state,
so metrics are a pure, policy-independent function of recorded events
(design §8.1 ``metrics.py``, §6.5 item 6).

Populations (design §5, §6.4), all in days measured from ``SimConfig.epoch``:

- **Cohort** = complaints with ``arrival ∈ [t_warm, t_end_obs]`` (warm-up
  arrivals are excluded from metric populations but still occupy the queue,
  so they appear in backlog and utilization).
- **Started subset S** = cohort members with ``start`` recorded (the engine
  guarantees ``start ≤ t_cut``); used for wait metrics M1–M3.
- **Completed subset** = cohort members with ``completion`` recorded (the
  engine guarantees ``completion ≤ t_cut``); used for M5/M9.

Censoring (design §5 M8) is mandatory: ``fc_start`` and ``fc_comp`` are
always computed alongside ``n_started``/``n_completed``/``cohort_size``.
By construction ``completion_rate == 1 - fc_comp`` — they are one quantity
rendered two ways (audit correction, design §5 M5) and are never presented
as independent corroboration.

Empty-subset semantics: a statistic whose subset is empty is ``None``
("no observations"), never ``0`` — a fabricated zero wait would misreport
perfect performance.

M4: the backlog curve ``B(t) = #{arrivals ≤ t} − #{service starts ≤ t}``
(queue-waiting jobs only; in-service jobs excluded) on the configured grid,
plus the scalar growth rate ``g_B = (B(t_end_obs) − B(t_warm)) / t_obs``
(complaints per day, observation window, endpoint difference — design §5).
``B(t)`` counts **all** arrivals/starts, including warm-up complaints,
because backlog is system state, not a cohort statistic.

M7: ``U`` = busy time intersected with ``[t_warm, t_end_obs]`` divided by
``c · t_obs``; a complaint still in service at ``t_cut`` contributes its
observed service through ``t_cut`` (which always covers the whole window
after its start, since ``t_cut ≥ t_end_obs``).

M6 (D3.2): M1–M3 restricted to the started cohort members carrying the
latent ``sim_is_high_priority`` designation (design §5): ``high_n_started``
plus mean/median/nearest-rank-p95 wait over that subset, ``None`` when the
subset is empty. The designation is a **simulated label only** — an
assumed-world behaviour measure, meaningless outside its scenario's
correlation regime (§3.7) and never real importance (§7.5).

Nearest-rank p95 (design §5 M3): sort ascending and take the element at
1-based rank ``ceil(0.95 · n)`` - a sample quantile with no interpolation.
"""

from __future__ import annotations

import math
import statistics
from bisect import bisect_right
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.services.simulation.model import RunResult, SimConfig

# Design §5 (censoring bullet): "If f_c^{start} > 0.1 the report stamps the
# wait metrics 'censored subset — steady-state interpretation invalid'."
# The threshold and the exact stamp text live here so the report generator
# applies one shared rule and the Markdown/JSON renderings cannot drift.
CENSORING_WARN_THRESHOLD = 0.1
CENSORING_WARNING_TEXT = (
    "censored subset — steady-state interpretation invalid"
)


def censoring_warning(fc_start: Optional[float]) -> Optional[str]:
    """The conditional censoring stamp for the wait metrics (design §5).

    Returns the stamp text when the fraction of cohort complaints that were
    never started by ``t_cut`` exceeds :data:`CENSORING_WARN_THRESHOLD`, and
    ``None`` otherwise.

    An **undefined** censoring fraction (``fc_start is None`` — an empty
    cohort, so there is no population to be censored) never produces the
    stamp: it is reported as ``not_evaluated`` instead. A stamp must never
    be asserted for a condition that was not evaluated.
    """
    if fc_start is None:
        return None
    if fc_start > CENSORING_WARN_THRESHOLD:
        return CENSORING_WARNING_TEXT
    return None


def nearest_rank_p95(values: Sequence[float]) -> Optional[float]:
    """Nearest-rank 95th percentile: sorted ascending, 1-based rank
    ``ceil(0.95 · n)`` (design §5 M3); ``None`` for an empty sample."""
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(0.95 * len(ordered))
    rank = min(max(rank, 1), len(ordered))
    return ordered[rank - 1]


def _describe(values: Sequence[float]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """(mean, median, nearest-rank p95); (None, None, None) when empty."""
    if not values:
        return None, None, None
    return (
        statistics.fmean(values),
        float(statistics.median(values)),
        nearest_rank_p95(values),
    )


@dataclass(frozen=True)
class Metrics:
    """Computed metrics for one run (design §5). Times are in days."""

    cohort_size: int
    n_started: int
    n_completed: int
    wait_mean_days: Optional[float]
    wait_median_days: Optional[float]
    wait_p95_days: Optional[float]
    flow_mean_days: Optional[float]
    flow_median_days: Optional[float]
    flow_p95_days: Optional[float]
    completion_rate: Optional[float]
    fc_start: Optional[float]
    fc_comp: Optional[float]
    backlog_series: Tuple[Tuple[float, int], ...]
    backlog_growth_rate: float
    utilization: float
    # M6 (design §5): high-priority waiting-time subset — simulated latent
    # label only, evaluation-side; never real importance (§7.5).
    high_n_started: int
    high_wait_mean_days: Optional[float]
    high_wait_median_days: Optional[float]
    high_wait_p95_days: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["backlog_series"] = [list(point) for point in self.backlog_series]
        data["time_unit"] = "days"
        return data


def _day(value: datetime, config: SimConfig) -> float:
    return config.datetime_to_day(value)


def _backlog_grid(config: SimConfig) -> List[float]:
    t_start = float(config.t_warm_days)
    t_end = config.t_end_obs_days
    step = float(config.backlog_grid_step_days)
    grid: List[float] = []
    t = t_start
    while t < t_end - 1e-12:
        grid.append(t)
        t += step
    grid.append(t_end)
    return grid


def compute_metrics(run_result: RunResult, config: SimConfig) -> Metrics:
    """Compute M1–M9 from one run (design §5), shared by every policy.

    Cohort/censoring rules are exactly those of design §5; see the module
    docstring for the empty-subset and ``completion_rate``/``fc_comp``
    identity rules. M6 uses the latent ``sim_is_high_priority`` flag copied
    into outcomes by the engine — evaluation-side only (§3.6).
    """
    t_warm = float(config.t_warm_days)
    t_end = config.t_end_obs_days
    t_cut = config.t_cut_days

    arrival_days_all = sorted(_day(o.arrival, config) for o in run_result.outcomes)
    start_days_all = sorted(
        _day(o.start, config) for o in run_result.outcomes if o.start is not None
    )

    cohort = [
        o
        for o in run_result.outcomes
        if t_warm <= _day(o.arrival, config) <= t_end
    ]
    started = [o for o in cohort if o.start is not None]
    completed = [o for o in cohort if o.completion is not None]

    waits = [
        (o.start - o.arrival).total_seconds() / 86400.0 for o in started
    ]
    flows = [
        (o.completion - o.arrival).total_seconds() / 86400.0
        for o in completed
    ]

    cohort_size = len(cohort)
    if cohort_size:
        completion_rate = len(completed) / cohort_size
        fc_start = 1.0 - len(started) / cohort_size
        fc_comp = 1.0 - completion_rate
    else:
        completion_rate = None
        fc_start = None
        fc_comp = None

    backlog_series: List[Tuple[float, int]] = []
    for t in _backlog_grid(config):
        waiting = (
            bisect_right(arrival_days_all, t) - bisect_right(start_days_all, t)
        )
        backlog_series.append((t, waiting))
    backlog_growth_rate = (
        backlog_series[-1][1] - backlog_series[0][1]
    ) / float(config.t_obs_days)

    busy_overlap = 0.0
    for outcome in run_result.outcomes:
        if outcome.start is None:
            continue
        busy_start = _day(outcome.start, config)
        busy_end = (
            _day(outcome.completion, config)
            if outcome.completion is not None
            else t_cut
        )
        overlap_start = max(busy_start, t_warm)
        overlap_end = min(busy_end, t_end)
        if overlap_end > overlap_start:
            busy_overlap += overlap_end - overlap_start
    utilization = busy_overlap / (float(config.c) * float(config.t_obs_days))

    wait_mean, wait_median, wait_p95 = _describe(waits)
    flow_mean, flow_median, flow_p95 = _describe(flows)

    # M6 (design §5): M1-M3 restricted to {i in S : sim_is_high_priority}.
    high_waits = [
        (o.start - o.arrival).total_seconds() / 86400.0
        for o in started
        if o.sim_is_high_priority
    ]
    high_mean, high_median, high_p95 = _describe(high_waits)

    return Metrics(
        cohort_size=cohort_size,
        n_started=len(started),
        n_completed=len(completed),
        wait_mean_days=wait_mean,
        wait_median_days=wait_median,
        wait_p95_days=wait_p95,
        flow_mean_days=flow_mean,
        flow_median_days=flow_median,
        flow_p95_days=flow_p95,
        completion_rate=completion_rate,
        fc_start=fc_start,
        fc_comp=fc_comp,
        backlog_series=tuple(backlog_series),
        backlog_growth_rate=backlog_growth_rate,
        utilization=utilization,
        high_n_started=len(high_waits),
        high_wait_mean_days=high_mean,
        high_wait_median_days=high_median,
        high_wait_p95_days=high_p95,
    )
