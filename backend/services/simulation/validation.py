"""Scope-limited M/M/1 engine validation (design §6.9).

Purpose: sanity-check the discrete-event engine and the clock arithmetic
that feed metric **M1** against a textbook M/M/1 result. Nothing more.

Applicability conditions for the steady-state M/M/1 formula (standard
queueing theory — *not* a repository claim):

- single server (``c = 1``);
- Poisson arrivals at rate ``λ``;
- i.i.d. exponential service times with rate ``μ = 1/E[S]``;
- FIFO discipline, infinite buffer, no abandonment;
- **stability ``ρ = λ/μ < 1``**.

The validated quantity is the expected **queue waiting time**
``E[W_q] = λ / (μ(μ − λ)) = ρ / (μ − λ)`` — exactly what M1 measures
(``start_i − arrival_i``). The expected time *in the system*
``E[W] = 1/(μ − λ)`` is reported separately and is **never** compared with
the empirical queue waiting time.

For ``ρ ≥ 1`` the single-server system has no steady state, so the formula
is **not** applicable: the analytic value is reported as ``null`` with an
explicit explanation instead of being extrapolated.

Acceptance criterion (§6.9 item 3): a two-sided 95 % Student-t interval
over the replication-level means of the queue waiting time must contain the
analytic value. The estimate, the interval and the analytic value are
recorded **regardless of whether the criterion is met**.

Limitations that accompany the result (§6.9): a passing check validates
only the FIFO/exponential/``c = 1``/``ρ < 1`` configuration and, indirectly,
the event loop and the clock arithmetic feeding M1. It does **not** validate
priority key handling, multi-server behaviour, non-exponential service,
``ρ > 1`` stress runs, the generator's recurrence/evidence paths, the metric
code for M4–M8, or any policy comparison. A passing M/M/1 check is
necessary, not sufficient.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import replace
from typing import Any, Dict, List, Optional

from backend.services.simulation.generate import generate_stream
from backend.services.simulation.metrics import compute_metrics
from backend.services.simulation.model import SimConfig
from backend.services.simulation.policies import FIFO
from backend.services.simulation.queue_sim import run_policy

# The fixed validation configuration of design §6.9 item 1.
VALIDATION_RHO = 0.8
VALIDATION_CELL_ID = "mm1-validation"

# Two-sided 95 % Student-t critical values (textbook constants; scipy is
# not a project dependency). Keyed by degrees of freedom.
T_CRITICAL_975: Dict[int, float] = {
    1: 12.706205,
    2: 4.302653,
    3: 3.182446,
    4: 2.776445,
    5: 2.570582,
    6: 2.446912,
    7: 2.364624,
    8: 2.306004,
    9: 2.262157,
    10: 2.228139,
    11: 2.200985,
    12: 2.178813,
    13: 2.160369,
    14: 2.144787,
    15: 2.131450,
    16: 2.119905,
    17: 2.109816,
    18: 2.100922,
    19: 2.093024,
    20: 2.085963,
    21: 2.079614,
    22: 2.073873,
    23: 2.068658,
    24: 2.063899,
    25: 2.059539,
    26: 2.055529,
    27: 2.051831,
    28: 2.048407,
    29: 2.045230,
    30: 2.042272,
    40: 2.021075,
    60: 2.000298,
    120: 1.979930,
}

# Normal limit (infinite degrees of freedom).
T_CRITICAL_975_INFINITE = 1.959964

# Design §6.9 item 4: the censoring fraction must be approximately zero
# for the comparison to be meaningful (M1 would otherwise be a censored
# subset).
FC_START_MAX_ALLOWED = 0.01

# Design §6.9 item 4: the CI is only informative when each replication
# contributes a substantial steady-state sample.
MIN_COHORT_SIZE = 100

# Design §6.9 item 2: a warm-up shorter than this many relaxation times is
# flagged as an insufficient-warm-up warning (never as a pass).
MIN_WARMUP_RELAXATION_RATIO = 10.0


def t_critical_975(degrees_of_freedom: int) -> float:
    """Two-sided 95 % Student-t critical value for ``degrees_of_freedom``.

    Uses the tabulated constant for the exact df when available and the
    nearest *smaller* tabulated df otherwise (conservative: never returns a
    smaller critical value than the exact one).
    """
    if degrees_of_freedom < 1:
        raise ValueError("degrees_of_freedom must be >= 1")
    if degrees_of_freedom in T_CRITICAL_975:
        return T_CRITICAL_975[degrees_of_freedom]
    smaller = [df for df in T_CRITICAL_975 if df < degrees_of_freedom]
    if not smaller:
        return T_CRITICAL_975_INFINITE
    return T_CRITICAL_975[max(smaller)]


def analytic_queue_wait_mean(
    rho: float,
    mu: float,
    lam: float,
    servers: int = 1,
    service_dist: str = "exponential",
) -> Optional[float]:
    """The M/M/1 steady-state mean queue waiting time, or ``None``.

    Returns ``E[W_q] = rho / (mu - lambda)`` only when the closed form
    applies: a single server, exponential service, and stability
    ``rho = lambda/mu < 1``.

    For ``rho >= 1`` the single-server system has **no steady state** and no
    finite steady-state waiting time exists, so ``None`` is returned — the
    formula is never evaluated, let alone extrapolated, outside its domain
    of validity.
    """
    if servers != 1 or service_dist != "exponential":
        return None
    if rho >= 1.0:
        return None
    if mu <= lam:
        return None
    return rho / (mu - lam)


def validation_config(config: SimConfig) -> SimConfig:
    """The §6.9 fixed validation configuration derived from the run config.

    Keeps the configured service distribution, service mean and window
    lengths; pins the load to ρ = 0.8 and drops any arrival cap so the
    horizon itself bounds the stream (§6.4). The correlation regime is set
    to ``independent`` because the validation is a FIFO run in which the
    generator's latent structure plays no part; the detector-visibility
    regime is irrelevant for FIFO (no evidence is consumed).
    """
    return replace(
        config,
        rho=VALIDATION_RHO,
        max_arrivals=None,
        correlation_regime="independent",
    )


def run_mm1_validation(config: SimConfig, replications: int = 30) -> Dict[str, Any]:
    """Run the §6.9 validation and return the recorded evidence block.

    The block is always produced — pass or fail — and is included in every
    report regardless of the outcome (design §6.9 item 3).
    """
    if replications < 2:
        raise ValueError("M/M/1 validation needs at least 2 replications for a CI")

    v_config = validation_config(config)
    mean_service = float(v_config.mean_service_days)
    mu = 1.0 / mean_service
    lam = v_config.lambda_per_day
    rho = float(v_config.rho)
    servers = int(v_config.c)
    exponential = v_config.service_dist == "exponential"
    stable = rho < 1.0

    applicable = bool(stable and servers == 1 and exponential)

    analytic_wait_mean: Optional[float] = None
    analytic_flow_mean: Optional[float] = None
    relaxation_time: Optional[float] = None
    warmup_ratio: Optional[float] = None
    if applicable:
        analytic_wait_mean = analytic_queue_wait_mean(
            rho, mu, lam, servers, v_config.service_dist
        )
        analytic_flow_mean = 1.0 / (mu - lam)
        relaxation_time = 1.0 / (mu - lam)
        warmup_ratio = float(v_config.t_warm_days) / relaxation_time

    estimates: List[float] = []
    cohort_sizes: List[int] = []
    fc_starts: List[float] = []
    fc_comps: List[float] = []
    for rep in range(replications):
        stream = generate_stream(v_config, rep, cell_id=VALIDATION_CELL_ID)
        result = run_policy(stream, v_config, FIFO)
        metrics = compute_metrics(result, v_config)
        if metrics.wait_mean_days is None:
            continue
        estimates.append(float(metrics.wait_mean_days))
        cohort_sizes.append(int(metrics.cohort_size))
        if metrics.fc_start is not None:
            fc_starts.append(float(metrics.fc_start))
        if metrics.fc_comp is not None:
            fc_comps.append(float(metrics.fc_comp))

    n_used = len(estimates)
    estimate: Optional[float] = None
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    half_width: Optional[float] = None
    t_critical: Optional[float] = None
    standard_error: Optional[float] = None
    if n_used >= 2:
        estimate = statistics.fmean(estimates)
        t_critical = t_critical_975(n_used - 1)
        standard_error = statistics.stdev(estimates) / math.sqrt(n_used)
        half_width = t_critical * standard_error
        ci_low = estimate - half_width
        ci_high = estimate + half_width

    covers_analytic = (
        applicable
        and analytic_wait_mean is not None
        and ci_low is not None
        and ci_high is not None
        and ci_low <= analytic_wait_mean <= ci_high
    )

    fc_start_max = max(fc_starts) if fc_starts else None
    fc_comp_max = max(fc_comps) if fc_comps else None
    min_cohort = min(cohort_sizes) if cohort_sizes else None

    preconditions = {
        "rho_less_than_1": {
            "passed": bool(stable),
            "detail": f"rho={rho} (stability requires rho < 1 for the M/M/1 steady state)",
        },
        "single_server": {
            "passed": servers == 1,
            "detail": f"c={servers}",
        },
        "exponential_service": {
            "passed": bool(exponential),
            "detail": f"service_dist={v_config.service_dist!r}",
        },
        "censoring_negligible": {
            "passed": fc_start_max is not None and fc_start_max <= FC_START_MAX_ALLOWED,
            "detail": (
                f"max f_c^(start) = {fc_start_max!r} "
                f"(threshold {FC_START_MAX_ALLOWED}); a non-negligible censoring "
                "fraction would make M1 a censored subset and invalidate the "
                "comparison"
            ),
        },
        "cohort_large_enough": {
            "passed": min_cohort is not None and min_cohort >= MIN_COHORT_SIZE,
            "detail": (
                f"minimum steady-state cohort size = {min_cohort!r} "
                f"(threshold {MIN_COHORT_SIZE})"
            ),
        },
        "warmup_covers_mixing": {
            "passed": (
                warmup_ratio is not None
                and warmup_ratio >= MIN_WARMUP_RELAXATION_RATIO
            ),
            "detail": (
                f"t_warm / (1/(mu-lambda)) = {warmup_ratio!r} days-equivalent "
                f"(threshold {MIN_WARMUP_RELAXATION_RATIO}); below the threshold "
                "the warm-up is flagged as insufficient, never as a pass"
            ),
        },
    }

    all_preconditions_pass = all(
        bool(entry["passed"]) for entry in preconditions.values()
    )

    return {
        "purpose": (
            "scope-limited engine validation against the textbook M/M/1 "
            "queue-wait expectation (design §6.9); sanity check only"
        ),
        "target_metric": "M1 (queue waiting time = service start - arrival)",
        "analytic_target": (
            "E[W_q] = rho / (mu - lambda); the time-in-system value "
            "E[W] = 1/(mu - lambda) is reported separately and never compared "
            "with the empirical queue waiting time"
        ),
        "configuration": {
            "rho": rho,
            "servers": servers,
            "service_dist": v_config.service_dist,
            "mean_service_days": mean_service,
            "mu_per_day": mu,
            "lambda_per_day": lam,
            "t_warm_days": float(v_config.t_warm_days),
            "t_obs_days": float(v_config.t_obs_days),
            "t_drain_max_days": float(v_config.t_drain_max_days),
            "t_cut_days": float(v_config.t_cut_days),
            "backlog_grid_step_days": float(v_config.backlog_grid_step_days),
            "master_seed": int(v_config.master_seed),
            "cell_id": VALIDATION_CELL_ID,
            "policy": "fifo",
            "max_arrivals": v_config.max_arrivals,
            "replications_requested": int(replications),
            "replications_used": n_used,
        },
        "replications": n_used,
        "estimate_queue_wait_mean_days": estimate,
        "confidence_interval_95_days": {
            "low": ci_low,
            "high": ci_high,
            "half_width": half_width,
            "method": "two-sided Student-t over the replication-level means",
            "t_critical_975": t_critical,
            "degrees_of_freedom": (n_used - 1) if n_used >= 2 else None,
            "standard_error": standard_error,
        },
        "analytic_queue_wait_mean_days": analytic_wait_mean,
        "analytic_time_in_system_days": analytic_flow_mean,
        "relaxation_time_days": relaxation_time,
        "warmup_over_relaxation_ratio": warmup_ratio,
        "applicability": {
            "applicable": applicable,
            "conditions": (
                "single server (c=1), Poisson arrivals, i.i.d. exponential "
                "service with mu = 1/E[S], FIFO, infinite buffer, no "
                "abandonment, and stability rho = lambda/mu < 1"
            ),
            "explanation": (
                "the M/M/1 steady-state queue-wait formula applies"
                if applicable
                else "the M/M/1 steady-state queue-wait formula does not apply "
                "to this configuration (requires c=1, exponential service and "
                "stability rho < 1); for rho >= 1 the single-server system has "
                "no steady state and no finite steady-state waiting time "
                "exists, so no analytic value is reported and none is "
                "extrapolated"
            ),
        },
        "criterion": {
            "rule": "the analytic E[W_q] must fall inside the 95 % Student-t interval",
            "covers_analytic_value": bool(covers_analytic),
            "reported_regardless_of_pass_or_fail": True,
        },
        "preconditions": preconditions,
        "all_preconditions_pass": all_preconditions_pass,
        "observed": {
            "cohort_size_min": min_cohort,
            "cohort_size_max": max(cohort_sizes) if cohort_sizes else None,
            "fc_start_max": fc_start_max,
            "fc_comp_max": fc_comp_max,
        },
        "limitations": [
            "A passing M/M/1 validation is necessary, not sufficient: it "
            "validates only the FIFO/exponential/c=1/rho<1 configuration and "
            "the clock arithmetic feeding M1 — never priority keys, "
            "multi-server behaviour, the generator, M4-M8, or any policy "
            "comparison.",
            "The validation is a property of the simulator, not of the "
            "policies: no policy comparison is implied or supported by it.",
            "It validates the assumed data-generating process only; it "
            "establishes nothing about real-world municipal performance.",
        ],
    }
