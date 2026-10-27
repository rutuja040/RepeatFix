"""Tests for the Phase D3 simulation (D3.1 foundation, D3.2 generator/evidence/metrics, D3.3 policies/experiment/reporting).

Covers this milestone's required validation: event ordering (Lindley FIFO
recurrence), queue invariants, deterministic replay, and cutoff/censoring
behaviour, plus hand-computed backlog/utilization checks and the §6.2 seed
derivation rule. No database fixtures are needed — the simulation is
stream-level.

D3.2 adds design-§8.4 coverage for the generator (tiny-arrival-rate
robustness, missingness, repeat-linked time/distance windows, correlation
regimes), the leakage-free evidence adapter (detector-visible/blind, D1
signal requirements, future-data isolation, production count-ranking
parity), the policy-visible information boundary, metric M6, and the
scope-limited M/M/1 engine validation (§6.9). The M/M/1 statistics are
textbook queueing results, not repository claims.
"""

from __future__ import annotations

import inspect
import math
import statistics
from dataclasses import fields as dataclass_fields, replace
from datetime import datetime, timedelta

import pytest

from backend.services.evaluation.baselines import count_based_ranking
from backend.services.evaluation.harness import (
    _select_candidates as _harness_select_candidates,
    _to_complaint_input as _harness_to_complaint_input,
)
from backend.services.imports.duplicates import (
    haversine_meters,
    normalize_text,
    text_similarity,
)
from backend.services.recurrence.service import (
    REQUIRED_SIGNALS,
    RecurrenceAnalysisService,
)
from backend.services.recurrence.types import (
    LOCATION_METERS_MAX,
    SIMILARITY_THRESHOLD,
    TIME_WINDOW_DAYS,
)
from backend.services.simulation.evidence import (
    EvidenceRelation,
    arrival_snapshot_evidence,
    filter_relations_as_of,
    is_recurrence_eligible,
    select_predecessors,
    snapshot_counts,
    to_complaint_input,
)
from backend.services.simulation.generate import (
    STREAM_NAMES,
    derive_stream_seed,
    generate_stream,
    make_rng_streams,
)
from backend.services.simulation.metrics import (
    compute_metrics,
    nearest_rank_p95,
)
from backend.services.simulation.model import (
    POLICY_VISIBLE_FIELDS,
    ArrivalStream,
    PolicyEvidence,
    SimComplaint,
    SimConfig,
)
from backend.services.simulation.policies import (
    FIFO,
    POLICIES,
    Policy,
    PolicySnapshot,
    fifo_key,
    make_policy,
    policies_for_config,
    rule_based_key,
    selection_rank,
    validate_policy,
)
from backend.services.simulation.queue_sim import _snapshot_of, run_policy

EPOCH = datetime(2026, 1, 1)


def _manual_stream(
    offsets,
    service_days=1.0,
    ids=None,
    *,
    master_seed=0,
    cell_id="",
    seed=0,
) -> ArrivalStream:
    """Build a sorted stream from day offsets (manual, no RNG involved)."""
    complaints = []
    for index, offset in enumerate(offsets):
        service = (
            service_days if isinstance(service_days, (int, float)) else service_days[index]
        )
        complaints.append(
            SimComplaint(
                complaint_id=ids[index] if ids else f"C{index:03d}",
                arrival=EPOCH + timedelta(days=float(offset)),
                service_days=float(service),
            )
        )
    return ArrivalStream(
        complaints=tuple(complaints),
        master_seed=master_seed,
        cell_id=cell_id,
        seed=seed,
    )


def _days(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 86400.0


def _first_report_roles(stream: ArrivalStream):
    """R_i recomputed independently from the latent issue structure.

    R_i is True iff complaint i is the first report of a latent issue that
    receives at least one repeat (design §3.7).
    """
    first_index = {}
    counts = {}
    for index, complaint in enumerate(stream.complaints):
        issue = complaint.latent_issue_id
        if issue not in first_index:
            first_index[issue] = index
        counts[issue] = counts.get(issue, 0) + 1
    flags = [False] * len(stream.complaints)
    for issue, index in first_index.items():
        if counts[issue] >= 2:
            flags[index] = True
    return flags


def _repeat_pairs(stream: ArrivalStream):
    """Consecutive same-issue report pairs, in stream order."""
    groups = {}
    for complaint in stream.complaints:
        groups.setdefault(complaint.latent_issue_id, []).append(complaint)
    for group in groups.values():
        for previous, following in zip(group, group[1:]):
            yield previous, following


def _three_sigma_proportion(p: float, n: int) -> float:
    """Sampling tolerance for proportion checks: three standard errors."""
    if n <= 0:
        return float("inf")
    return 3.0 * math.sqrt(max(p * (1.0 - p), 1e-12) / n)


def _point_at_meters(lat: float, lon: float, meters: float):
    """A point due north of (lat, lon) at an approximate ground distance."""
    delta_deg = meters * 180.0 / (math.pi * 6371000.0)
    return lat + delta_deg, lon


class TestConfig:
    def test_documented_design_defaults(self):
        config = SimConfig()
        assert config.mean_service_days == 0.2
        assert config.c == 1
        assert config.t_warm_days == 30.0
        assert config.t_obs_days == 180.0
        assert config.t_drain_max_days == 90.0
        assert config.service_dist == "exponential"
        assert config.t_end_obs_days == 210.0
        assert config.t_cut_days == 300.0

    def test_lambda_is_derived_from_rho_c_and_e_s(self):
        config = SimConfig(rho=0.8)
        assert config.lambda_per_day == pytest.approx(0.8 * 1 / 0.2)
        assert SimConfig(rho=1.2).lambda_per_day == pytest.approx(6.0)
        assert SimConfig(rho=0.5).lambda_per_day == pytest.approx(2.5)

    def test_config_echo_marks_placeholder_and_units(self):
        data = SimConfig().to_dict()
        assert data["time_unit"] == "days"
        assert "placeholder" in data["mean_service_days_status"]
        assert "not calibrated" in data["mean_service_days_status"]
        assert data["epoch"] == EPOCH.isoformat()

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"c": 2},
            {"rho": 0.0},
            {"rho": -1.0},
            {"mean_service_days": 0.0},
            {"service_dist": "weibull"},
            {"t_warm_days": -1.0},
            {"t_obs_days": 0.0},
            {"t_drain_max_days": -1.0},
            {"max_arrivals": 0},
            {"backlog_grid_step_days": 0.0},
        ],
    )
    def test_invalid_config_rejected(self, kwargs):
        with pytest.raises(ValueError):
            SimConfig(**kwargs)


class TestSeedDerivation:
    def test_sha256_known_answer(self):
        assert derive_stream_seed(0, "", 0, "arrivals") == 16413707623107878639
        assert derive_stream_seed(0, "", 0, "service") == 14111906130963185089

    def test_streams_separated_by_every_derivation_component(self):
        values = {
            derive_stream_seed(0, "", 0, "arrivals"),
            derive_stream_seed(0, "", 0, "service"),
            derive_stream_seed(1, "", 0, "arrivals"),
            derive_stream_seed(0, "cell", 0, "arrivals"),
            derive_stream_seed(0, "", 1, "arrivals"),
        }
        assert len(values) == 5

    def test_all_design_stream_names_present(self):
        assert STREAM_NAMES == (
            "arrivals",
            "service",
            "attrs",
            "recurrence",
            "evidence",
            "missing",
        )
        rngs = make_rng_streams(master_seed=7, rep=3)
        assert set(rngs) == set(STREAM_NAMES)
        first_draws = [rngs[name].random() for name in STREAM_NAMES]
        assert len(set(first_draws)) == len(STREAM_NAMES)


class TestGenerateStream:
    def _config(self, **overrides):
        defaults = dict(rho=0.8, master_seed=42, mean_service_days=0.2)
        defaults.update(overrides)
        return SimConfig(**defaults)

    def test_deterministic_generation(self):
        config = self._config()
        first = generate_stream(config, 7)
        second = generate_stream(config, 7)
        assert first == second
        assert first.checksum() == second.checksum()

    def test_different_seed_changes_stream(self):
        config = self._config()
        assert generate_stream(config, 7).checksum() != generate_stream(
            config, 8
        ).checksum()

    def test_arrivals_within_horizon_sorted_and_unique(self):
        config = self._config()
        stream = generate_stream(config, 7)
        horizon = config.day_to_datetime(config.t_end_obs_days)
        assert stream.complaints, "expected a non-empty stream"
        previous = None
        ids = set()
        for complaint in stream.complaints:
            assert complaint.arrival >= config.epoch
            assert complaint.arrival < horizon
            assert complaint.service_days >= 0
            assert complaint.complaint_id not in ids
            ids.add(complaint.complaint_id)
            if previous is not None:
                assert complaint.arrival >= previous
            previous = complaint.arrival

    def test_max_arrivals_cap(self):
        config = self._config(max_arrivals=50)
        stream = generate_stream(config, 7)
        assert len(stream.complaints) == 50

    def test_service_distributions(self):
        exponential = generate_stream(self._config(), 7)
        services = [c.service_days for c in exponential.complaints]
        assert len(set(services)) > 1
        constant = generate_stream(self._config(service_dist="constant"), 7)
        assert {c.service_days for c in constant.complaints} == {0.2}

    def test_empirical_interarrival_mean_matches_derived_lambda(self):
        config = self._config()
        stream = generate_stream(config, 7)
        offsets = [config.datetime_to_day(c.arrival) for c in stream.complaints]
        gaps = [offsets[0]] + [
            offsets[i] - offsets[i - 1] for i in range(1, len(offsets))
        ]
        expected_gap = 1.0 / config.lambda_per_day
        assert sum(gaps) / len(gaps) == pytest.approx(expected_gap, rel=0.10)


class TestFifoPolicy:
    def test_fifo_key_and_rank(self):
        created = datetime(2026, 3, 1, 8, 30)
        snapshot = PolicySnapshot(complaint_id="A-1", created_at=created)
        assert fifo_key(snapshot) == (0.0, created, "A-1")
        assert selection_rank(FIFO, snapshot) == (0.0, created, "A-1")
        assert "fifo" in POLICIES
        assert set(POLICIES) == {"fifo", "rule_based", "count_based", "recurrence_aware"}

    def test_simultaneous_waiters_break_ties_by_complaint_id(self):
        config = SimConfig(t_warm_days=0.0, t_obs_days=10.0, t_drain_max_days=10.0)
        stream = _manual_stream(
            [0.0, 0.5, 0.5],
            service_days=1.0,
            ids=["X-lead", "z-late", "a-early"],
        )
        result = run_policy(stream, config, FIFO)
        lead, z_last, a_first = (
            result.outcome("X-lead"),
            result.outcome("z-late"),
            result.outcome("a-early"),
        )
        assert lead.start == config.epoch
        assert a_first.start == EPOCH + timedelta(days=1.0)
        assert z_last.start == EPOCH + timedelta(days=2.0)

    def test_unknown_policy_rejected(self):
        config = SimConfig()
        stream = _manual_stream([0.1])
        rogue = Policy(name="rogue", key=fifo_key)
        with pytest.raises(ValueError):
            run_policy(stream, config, rogue)


class TestEventOrdering:
    def test_lindley_recurrence_exact_for_constant_service(self):
        """Design §8.4 test 3: c = 1, constant service, W follows Lindley."""
        config = SimConfig(
            t_warm_days=0.0, t_obs_days=10.0, t_drain_max_days=10.0,
            mean_service_days=0.2, service_dist="constant",
        )
        offsets = [0.0, 0.1, 0.3, 0.6, 0.75, 1.5]
        service = 0.2
        stream = _manual_stream(offsets, service_days=service)
        result = run_policy(stream, config, FIFO)

        expected_waits = [0.0]
        for index in range(1, len(offsets)):
            gap = offsets[index] - offsets[index - 1]
            expected_waits.append(
                max(0.0, expected_waits[-1] + service - gap)
            )

        for index, offset in enumerate(offsets):
            outcome = result.outcome(f"C{index:03d}")
            assert outcome.start is not None
            assert outcome.completion is not None
            assert _days(EPOCH + timedelta(days=offset), outcome.start) == pytest.approx(
                expected_waits[index], abs=1e-9
            )
            assert _days(outcome.start, outcome.completion) == pytest.approx(
                service, abs=1e-9
            )


class TestQueueInvariants:
    def test_invariants_on_generated_stream(self):
        config = SimConfig(rho=0.8, master_seed=42, mean_service_days=0.2)
        stream = generate_stream(config, 7)
        result = run_policy(stream, config, FIFO)
        t_cut = config.day_to_datetime(config.t_cut_days)

        started = [o for o in result.outcomes if o.start is not None]
        assert started, "expected started complaints at rho=0.8"

        for outcome in result.outcomes:
            if outcome.start is None:
                assert outcome.completion is None
                continue
            assert outcome.start >= outcome.arrival
            assert outcome.completion is not None
            assert outcome.completion >= outcome.start
            assert _days(outcome.start, outcome.completion) == pytest.approx(
                outcome.service_days, abs=1e-9
            )
            assert outcome.start <= t_cut
            assert outcome.completion <= t_cut

        by_start = sorted(started, key=lambda o: (o.start, o.complaint_id))
        by_arrival = sorted(started, key=lambda o: (o.arrival, o.complaint_id))
        assert [o.complaint_id for o in by_start] == [
            o.complaint_id for o in by_arrival
        ]

        events = []
        for outcome in started:
            assert outcome.completion is not None
            events.append((outcome.completion, 0, -1))
            events.append((outcome.start, 1, 1))
        events.sort(key=lambda item: (item[0], item[1]))
        in_service = 0
        for _, _, delta in events:
            in_service += delta
            assert in_service <= 1, "single server must never run two jobs"


class TestCutoffAndCensoring:
    def _config(self):
        return SimConfig(
            rho=0.5,
            mean_service_days=1.0,
            t_warm_days=1.0,
            t_obs_days=2.0,
            t_drain_max_days=0.5,
        )

    def _stream(self):
        return _manual_stream(
            [0.5, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2], service_days=1.0
        )

    def test_hand_computed_censoring_and_waiting_times(self):
        config = self._config()
        result = run_policy(self._stream(), config, FIFO)
        metrics = compute_metrics(result, config)

        assert result.stopped_at == EPOCH + timedelta(days=3.5)
        assert metrics.cohort_size == 7
        assert metrics.n_started == 3
        assert metrics.n_completed == 2
        assert metrics.fc_start == pytest.approx(4.0 / 7.0)
        assert metrics.completion_rate == pytest.approx(2.0 / 7.0)
        assert metrics.fc_comp == pytest.approx(5.0 / 7.0)
        assert metrics.wait_mean_days == pytest.approx(1.3, abs=1e-9)
        assert metrics.wait_median_days == pytest.approx(1.3, abs=1e-9)
        assert metrics.flow_mean_days == pytest.approx(1.9, abs=1e-9)

        censored_wait = result.outcome("C004")
        assert censored_wait.start is None
        assert censored_wait.completion is None
        in_service = result.outcome("C003")
        assert in_service.start is not None
        assert in_service.start <= result.stopped_at
        assert in_service.completion is None

    def test_no_event_beyond_t_cut(self):
        config = self._config()
        result = run_policy(self._stream(), config, FIFO)
        t_cut = config.day_to_datetime(config.t_cut_days)
        for outcome in result.outcomes:
            assert outcome.start is None or outcome.start <= t_cut
            assert outcome.completion is None or outcome.completion <= t_cut

    def test_completion_rate_identity_with_fc_comp(self):
        config = self._config()
        metrics = compute_metrics(run_policy(self._stream(), config, FIFO), config)
        assert metrics.fc_comp == 1.0 - metrics.completion_rate

    def test_hand_computed_backlog_and_utilization(self):
        config = self._config()
        metrics = compute_metrics(run_policy(self._stream(), config, FIFO), config)
        assert metrics.backlog_series == ((1.0, 1), (2.0, 5), (3.0, 5))
        assert metrics.backlog_growth_rate == pytest.approx(2.0)
        assert metrics.utilization == pytest.approx(1.0)
        assert 0.0 <= metrics.utilization <= 1.0

    def test_arrival_after_horizon_rejected(self):
        config = SimConfig(t_warm_days=1.0, t_obs_days=1.0, t_drain_max_days=0.5)
        stream = _manual_stream([1.0, 2.5], service_days=0.5)
        with pytest.raises(ValueError):
            run_policy(stream, config, FIFO)


class TestDeterministicReplay:
    def test_run_policy_signature_is_rng_free(self):
        signature = inspect.signature(run_policy)
        params = tuple(signature.parameters)
        assert params == ("stream", "config", "policy", "evidence")
        # The D3.3 evidence bundle is a frozen, pre-computed data mapping —
        # never an RNG, seed, or generator handle (design §6.2: policy
        # execution must not consume or shift any generator stream).
        assert (
            signature.parameters["evidence"].kind
            is inspect.Parameter.KEYWORD_ONLY
        )

    def test_same_inputs_same_result_and_stream_untouched(self):
        config = SimConfig(rho=0.8, master_seed=42)
        stream = generate_stream(config, 7)
        checksum_before = stream.checksum()
        first = run_policy(stream, config, FIFO)
        second = run_policy(stream, config, FIFO)
        assert first == second
        assert stream.checksum() == checksum_before

    def test_replay_across_processes_is_seed_defined(self):
        config = SimConfig(rho=0.8, master_seed=42)
        stream = generate_stream(config, 7)
        result = run_policy(stream, config, FIFO)
        rebuilt = generate_stream(SimConfig(rho=0.8, master_seed=42), 7)
        assert rebuilt.checksum() == stream.checksum()
        rerun = run_policy(rebuilt, config, FIFO)
        assert rerun == result


class TestMetricsEdgeCases:
    def test_nearest_rank_p95(self):
        assert nearest_rank_p95([]) is None
        assert nearest_rank_p95([5.0]) == 5.0
        assert nearest_rank_p95(list(range(1, 21))) == 19
        assert nearest_rank_p95(list(range(1, 101))) == 95

    def test_empty_cohort_reports_none_not_zero(self):
        config = SimConfig(t_warm_days=5.0, t_obs_days=10.0, t_drain_max_days=5.0)
        stream = _manual_stream([1.0, 2.0, 3.0], service_days=0.5)
        metrics = compute_metrics(run_policy(stream, config, FIFO), config)
        assert metrics.cohort_size == 0
        assert metrics.n_started == 0
        assert metrics.wait_mean_days is None
        assert metrics.completion_rate is None
        assert metrics.fc_start is None
        assert metrics.fc_comp is None
        assert metrics.utilization == 0.0

    def test_echoed_production_constants_match_live_imports(self):
        data = SimConfig().to_dict()
        echoed = data["production_constants"]
        assert echoed["SIMILARITY_THRESHOLD"] == SIMILARITY_THRESHOLD
        assert echoed["LOCATION_METERS_MAX"] == LOCATION_METERS_MAX
        assert echoed["TIME_WINDOW_DAYS"] == TIME_WINDOW_DAYS
        assert echoed["REQUIRED_SIGNALS"] == sorted(REQUIRED_SIGNALS)


class TestArrivalOffsetRobustness:
    """Design §3.2/§6.4 (D3.2 audit item): the floating-point day offset is
    compared against the configured horizon *before* it is converted to a
    datetime, so an extreme inter-arrival gap at a tiny arrival rate can
    never overflow ``timedelta``/``datetime``."""

    @pytest.mark.parametrize("rho", [1e-8, 1e-12, 1e-320])
    def test_tiny_arrival_rate_respects_horizon_without_overflow(self, rho, monkeypatch):
        config = SimConfig(rho=rho, mean_service_days=1.0, master_seed=0)
        assert config.lambda_per_day < 1.0 / config.t_end_obs_days

        converted = []
        original = SimConfig.day_to_datetime

        def spy(self, day_offset):
            converted.append(day_offset)
            return original(self, day_offset)

        monkeypatch.setattr(SimConfig, "day_to_datetime", spy)
        stream = generate_stream(config, 0)
        monkeypatch.undo()

        # No conversion ever exceeds the configured horizon (the pre-D3.2
        # code converted the raw offset first and could raise OverflowError).
        assert converted
        assert all(day <= config.t_end_obs_days for day in converted)
        horizon = config.day_to_datetime(config.t_end_obs_days)
        for complaint in stream.complaints:
            assert complaint.arrival >= config.epoch
            assert complaint.arrival < horizon


class TestMissingnessMechanisms:
    """Design §3.6 missingness injection: coordinate/category drop rates are
    generator knobs exercised against the *unmodified* production paths."""

    def _config(self, **overrides):
        base = dict(
            rho=1.0, master_seed=11, max_arrivals=1000, t_obs_days=180.0
        )
        base.update(overrides)
        return SimConfig(**base)

    def test_zero_rates_drop_nothing(self):
        stream = generate_stream(self._config(), 3)
        assert stream.complaints
        for complaint in stream.complaints:
            assert complaint.latitude is not None
            assert complaint.longitude is not None
            assert complaint.category is not None

    def test_full_rates_drop_all_coordinates_and_category(self):
        stream = generate_stream(
            self._config(missing_coord_rate=1.0, missing_category_rate=1.0), 3
        )
        assert stream.complaints
        for complaint in stream.complaints:
            assert complaint.latitude is None
            assert complaint.longitude is None
            assert complaint.category is None

    def test_configured_rates_reproduce_requested_missingness(self):
        config = self._config(missing_coord_rate=0.2, missing_category_rate=0.2)
        stream = generate_stream(config, 3)
        n = len(stream.complaints)
        assert n >= 500
        missing_coords = sum(1 for c in stream.complaints if c.latitude is None)
        missing_category = sum(1 for c in stream.complaints if c.category is None)
        # Coordinates drop as a pair (never one of the two).
        for complaint in stream.complaints:
            assert (complaint.latitude is None) == (complaint.longitude is None)
        tolerance = _three_sigma_proportion(0.2, n)
        assert abs(missing_coords / n - 0.2) <= tolerance
        assert abs(missing_category / n - 0.2) <= tolerance

    def test_missing_coordinates_or_category_disable_evidence_and_counts(self):
        config = self._config(
            missing_coord_rate=1.0, missing_category_rate=1.0, max_arrivals=200
        )
        stream = generate_stream(config, 3)
        records = [c.to_record() for c in stream.complaints]
        assert not any(is_recurrence_eligible(record) for record in records)
        evidence = arrival_snapshot_evidence(stream, config)
        assert all(not item.relations for item in evidence.values())
        counts = snapshot_counts(stream.complaints)
        assert counts and all(count == 0 for count in counts.values())


class TestRepeatLinkedGeneration:
    """Design §3.2/§3.5: repeats of one latent issue arrive within the
    production time window, sit inside the production distance window, and
    share the issue's category — the conditions under which the D1
    detector *can* fire."""

    def _config(self, **overrides):
        base = dict(rho=0.8, master_seed=5, p_repeat=0.7, max_arrivals=300)
        base.update(overrides)
        return SimConfig(**base)

    def test_consecutive_repeats_respect_time_distance_and_category(self):
        stream = generate_stream(self._config(), 3)
        pairs = list(_repeat_pairs(stream))
        assert pairs, "expected latent repeats at p_repeat=0.7"
        for previous, following in pairs:
            gap_days = _days(previous.arrival, following.arrival)
            assert 0.0 < gap_days <= TIME_WINDOW_DAYS
            distance = haversine_meters(
                previous.latitude,
                previous.longitude,
                following.latitude,
                following.longitude,
            )
            assert distance <= LOCATION_METERS_MAX
            assert previous.category == following.category

    def test_realized_diagnostics_match_independent_recomputation(self):
        stream = generate_stream(self._config(), 3)
        n = len(stream.complaints)
        flags = _first_report_roles(stream)
        high = [c.sim_is_high_priority for c in stream.complaints]
        n_r1 = sum(flags)
        assert stream.n_latent_issues == len({c.latent_issue_id for c in stream.complaints})
        assert stream.realized_pi == pytest.approx(n_r1 / n)
        assert stream.realized_repeat_fraction == pytest.approx(
            1.0 - stream.n_latent_issues / n
        )
        assert stream.realized_p_high == pytest.approx(sum(high) / n)
        r1_high = [high[i] for i in range(n) if flags[i]]
        r0_high = [high[i] for i in range(n) if not flags[i]]
        assert stream.realized_p_high_given_r1 == pytest.approx(sum(r1_high) / len(r1_high))
        assert stream.realized_p_high_given_r0 == pytest.approx(sum(r0_high) / len(r0_high))

    def test_p_repeat_zero_yields_no_repeats(self):
        stream = generate_stream(self._config(p_repeat=0.0, max_arrivals=100), 3)
        n = len(stream.complaints)
        assert n == 100
        assert stream.n_latent_issues == n
        assert stream.realized_repeat_fraction == 0.0
        assert stream.realized_pi == 0.0
        assert stream.realized_p_high_given_r1 is None


class TestCorrelationRegimes:
    """Design §3.7: the correlation regimes are exactly marginal-preserving
    generation rules with a feasibility check that never clips."""

    def _config(self, **overrides):
        base = dict(
            rho=0.8, master_seed=3, p_repeat=0.4, p_high=0.25, max_arrivals=1200
        )
        base.update(overrides)
        return SimConfig(**base)

    def test_independent_regime_is_uncorrelated_with_r(self):
        config = self._config(correlation_regime="independent")
        stream = generate_stream(config, 5)
        n = len(stream.complaints)
        flags = _first_report_roles(stream)
        n_r1 = sum(flags)
        n_r0 = n - n_r1
        assert n_r1 > 50 and n_r0 > 50
        assert abs(stream.realized_p_high - config.p_high) <= _three_sigma_proportion(
            config.p_high, n
        )
        assert abs(
            stream.realized_p_high_given_r1 - config.p_high
        ) <= _three_sigma_proportion(config.p_high, n_r1)
        assert abs(
            stream.realized_p_high_given_r0 - config.p_high
        ) <= _three_sigma_proportion(config.p_high, n_r0)

    def test_correlated_regime_matches_conditional_targets(self):
        config = self._config(
            p_high=0.2, correlation_delta=0.2, correlation_regime="correlated"
        )
        stream = generate_stream(config, 5)
        n = len(stream.complaints)
        flags = _first_report_roles(stream)
        n_r1 = sum(flags)
        n_r0 = n - n_r1
        theta_c = min(1.0, config.p_high + config.correlation_delta)
        theta_n = (config.p_high - theta_c * stream.realized_pi) / (1.0 - stream.realized_pi)
        assert 0.0 <= theta_n <= 1.0
        assert abs(stream.realized_p_high - config.p_high) <= _three_sigma_proportion(
            config.p_high, n
        )
        assert abs(
            stream.realized_p_high_given_r1 - theta_c
        ) <= _three_sigma_proportion(theta_c, n_r1)
        assert abs(
            stream.realized_p_high_given_r0 - theta_n
        ) <= _three_sigma_proportion(theta_n, n_r0)

    def test_anti_correlated_regime_matches_conditional_targets(self):
        config = self._config(
            p_high=0.2, correlation_delta=0.1, correlation_regime="anti_correlated"
        )
        stream = generate_stream(config, 5)
        n = len(stream.complaints)
        flags = _first_report_roles(stream)
        n_r1 = sum(flags)
        n_r0 = n - n_r1
        theta_c = max(0.0, config.p_high - config.correlation_delta)
        theta_n = (config.p_high - theta_c * stream.realized_pi) / (1.0 - stream.realized_pi)
        assert 0.0 <= theta_n <= 1.0
        assert abs(stream.realized_p_high - config.p_high) <= _three_sigma_proportion(
            config.p_high, n
        )
        assert abs(
            stream.realized_p_high_given_r1 - theta_c
        ) <= _three_sigma_proportion(theta_c, n_r1)
        assert abs(
            stream.realized_p_high_given_r0 - theta_n
        ) <= _three_sigma_proportion(theta_n, n_r0)

    def test_infeasible_theta_n_raises_instead_of_clipping(self):
        config = self._config(
            p_high=0.0, correlation_delta=0.5, correlation_regime="correlated",
            p_repeat=0.6,
        )
        with pytest.raises(ValueError, match="infeasible"):
            generate_stream(config, 5)

    def test_degenerate_pi_raises_for_non_independent_regime(self):
        config = self._config(p_repeat=0.0, correlation_regime="correlated",
                              max_arrivals=100)
        with pytest.raises(ValueError, match="0 < pi < 1"):
            generate_stream(config, 5)


class TestEvidenceGeneration:
    """Design §3.5/§4.3/§8.3: leakage-free arrival-snapshot evidence via the
    unmodified production detector, predecessor-only, cutoff-enforced."""

    def _config(self, **overrides):
        base = dict(
            rho=0.8, master_seed=7, p_repeat=0.5,
            detector_visibility="detector_visible", max_arrivals=150,
        )
        base.update(overrides)
        return SimConfig(**base)

    def test_detector_visible_stream_produces_relations_without_exact_source(self):
        config = self._config()
        stream = generate_stream(config, 7)
        assert all(c.raw_imported_values is None for c in stream.complaints)
        evidence = arrival_snapshot_evidence(stream, config)
        relations = [
            relation
            for item in evidence.values()
            for relation in item.relations
        ]
        assert relations
        assert all(r.relation_type == "potential_recurrence" for r in relations)
        assert not any(r.relation_type == "exact_source" for r in relations)

    def test_detector_blind_stream_produces_no_relations(self):
        config = self._config(detector_visibility="detector_blind")
        stream = generate_stream(config, 7)
        evidence = arrival_snapshot_evidence(stream, config)
        assert any(item.relations for item in evidence.values()) is False
        # Text-blindness hides the D1 detector only: the count-based adapter
        # never looks at text, so spatio-temporal-category counts remain
        # non-zero by construction (design §3.5 measures the cost of D1
        # degeneration for the D1-based policies, not for count-based).
        counts = snapshot_counts(stream.complaints)
        assert any(count > 0 for count in counts.values())

    def test_visibility_regimes_share_streams_except_text(self):
        visible = generate_stream(self._config(), 7)
        blind = generate_stream(self._config(detector_visibility="detector_blind"), 7)
        assert [c.arrival for c in visible.complaints] == [
            c.arrival for c in blind.complaints
        ]
        assert [c.service_days for c in visible.complaints] == [
            c.service_days for c in blind.complaints
        ]
        assert [c.latent_issue_id for c in visible.complaints] == [
            c.latent_issue_id for c in blind.complaints
        ]
        assert [c.sim_is_high_priority for c in visible.complaints] == [
            c.sim_is_high_priority for c in blind.complaints
        ]
        assert any(
            a.description != b.description
            for a, b in zip(visible.complaints, blind.complaints)
        )

    def test_production_text_similarity_confirms_detector_visibility(self):
        """Regime construction verified against the *real* text pipeline."""
        for visibility, below in (("detector_visible", False), ("detector_blind", True)):
            stream = generate_stream(
                self._config(detector_visibility=visibility), 7
            )
            similarities = [
                text_similarity(
                    normalize_text(previous.title, previous.description or ""),
                    normalize_text(following.title, following.description or ""),
                )
                for previous, following in _repeat_pairs(stream)
            ]
            assert similarities
            if below:
                assert max(similarities) < SIMILARITY_THRESHOLD
            else:
                assert min(similarities) >= SIMILARITY_THRESHOLD

    def test_relations_satisfy_required_signals_and_predecessor_only(self):
        config = self._config()
        stream = generate_stream(config, 7)
        checksum_before = stream.checksum()
        evidence = arrival_snapshot_evidence(stream, config)
        order = {
            complaint.complaint_id: index
            for index, complaint in enumerate(stream.complaints)
        }
        records = {c.complaint_id: c.to_record() for c in stream.complaints}
        by_id = {c.complaint_id: c for c in stream.complaints}
        found = 0
        for complaint in stream.complaints:
            for relation in evidence[complaint.complaint_id].relations:
                found += 1
                counterpart = by_id[relation.related_complaint_id]
                assert order[counterpart.complaint_id] < order[complaint.complaint_id]
                assert is_recurrence_eligible(records[counterpart.complaint_id])
                assert relation.relation_type == "potential_recurrence"
                assert REQUIRED_SIGNALS.issubset(set(relation.reason_codes))
                similarity = text_similarity(
                    normalize_text(complaint.title, complaint.description or ""),
                    normalize_text(counterpart.title, counterpart.description or ""),
                )
                assert similarity >= SIMILARITY_THRESHOLD
                assert (
                    complaint.category.strip().lower()
                    == counterpart.category.strip().lower()
                )
                assert (
                    haversine_meters(
                        complaint.latitude,
                        complaint.longitude,
                        counterpart.latitude,
                        counterpart.longitude,
                    )
                    <= LOCATION_METERS_MAX
                )
                assert _days(counterpart.arrival, complaint.arrival) <= TIME_WINDOW_DAYS
        assert found
        # Evidence construction never mutates or merges complaints.
        assert stream.checksum() == checksum_before

    def test_future_complaint_never_changes_earlier_evidence(self):
        config = self._config(max_arrivals=120)
        stream = generate_stream(config, 7)
        before = arrival_snapshot_evidence(stream, config)
        template = stream.complaints[3]
        future = SimComplaint(
            complaint_id="SIM-FUTURE-001",
            arrival=stream.complaints[-1].arrival + timedelta(days=0.5),
            service_days=0.1,
            title=template.title,
            description=template.description,
            category=template.category,
            latitude=template.latitude,
            longitude=template.longitude,
            administrative_area=template.administrative_area,
        )
        extended = ArrivalStream(
            complaints=tuple(list(stream.complaints) + [future]),
            master_seed=stream.master_seed,
            cell_id=stream.cell_id,
            seed=stream.seed,
        )
        after = arrival_snapshot_evidence(extended, config)
        for complaint in stream.complaints:
            assert before[complaint.complaint_id] == after[complaint.complaint_id]
        for relation in after[future.complaint_id].relations:
            assert relation.related_complaint_id in before

    def test_filter_relations_as_of_keeps_only_history(self):
        config = self._config()
        stream = generate_stream(config, 7)
        evidence = arrival_snapshot_evidence(stream, config)
        arrivals = {c.complaint_id: c.arrival for c in stream.complaints}
        with_relations = next(
            item for item in evidence.values() if item.relations
        )
        first = with_relations.relations[0]
        counterpart_arrival = arrivals[first.related_complaint_id]
        # Every counterpart in a frozen arrival snapshot is a predecessor, so
        # the whole relation set is visible once the latest counterpart has
        # arrived (design §3.5: the filter is a defensive assertion).
        assert filter_relations_as_of(
            with_relations, arrivals, max(arrivals.values())
        ) == with_relations.relations
        assert (
            first
            not in filter_relations_as_of(
                with_relations, arrivals, counterpart_arrival - timedelta(seconds=1)
            )
        )
        # A relation to a complaint that has not arrived yet is filtered out:
        # the decision-time boundary holds even for hand-built evidence.
        future_arrival = max(arrivals.values()) + timedelta(days=1)
        forged = EvidenceRelation(
            related_complaint_id="SIM-NOT-YET",
            relation_type="potential_recurrence",
            confidence_explained=1.0,
            reason_codes=("text_similarity",),
        )
        forged_evidence = type(with_relations)(
            complaint_id=with_relations.complaint_id, relations=(forged,)
        )
        extended_arrivals = dict(arrivals)
        extended_arrivals["SIM-NOT-YET"] = future_arrival
        assert filter_relations_as_of(
            forged_evidence, extended_arrivals, future_arrival - timedelta(seconds=1)
        ) == ()
        assert len(
            filter_relations_as_of(forged_evidence, extended_arrivals, future_arrival)
        ) == 1

    def test_evidence_matches_direct_production_service_call(self):
        """The adapter's output is exactly what the unmodified production
        service returns for the same predecessor pool and cutoff."""
        config = self._config(max_arrivals=60)
        stream = generate_stream(config, 7)
        evidence = arrival_snapshot_evidence(stream, config)
        service = RecurrenceAnalysisService()
        ordered = sorted(stream.complaints, key=lambda c: (c.arrival, c.complaint_id))
        for index, complaint in enumerate(ordered[:25]):
            record = complaint.to_record()
            predecessors = [
                earlier.to_record() for earlier in ordered[:index]
                if is_recurrence_eligible(earlier.to_record())
            ]
            candidates = select_predecessors(record, predecessors, config.max_candidates)
            analysis = service.analyze(
                to_complaint_input(record),
                [to_complaint_input(candidate) for candidate in candidates],
                cutoff=complaint.arrival,
            )
            expected = [
                (relation.related_complaint_id, relation.relation_type)
                for relation in analysis.relations
            ]
            actual = [
                (relation.related_complaint_id, relation.relation_type)
                for relation in evidence[complaint.complaint_id].relations
            ]
            assert actual == expected

    def test_runtime_evidence_code_never_imports_harness_privates(self):
        import ast

        import backend.services.simulation.evidence as evidence_module

        assert not hasattr(evidence_module, "_select_candidates")
        assert not hasattr(evidence_module, "_to_complaint_input")
        source = inspect.getsource(evidence_module)
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        assert not any("evaluation.harness" in name for name in imported)
        assert "evaluation.harness" not in source.split('"""')[1]


class TestProductionParity:
    """Design §8.3/§8.4.16: the simulation-local adapters must match the
    production count-based semantics and the harness helpers' behaviour.
    The harness private helpers are used only as a frozen test oracle."""

    def _pair(self, arrival_days, lat=52.5, lon=13.4, category="roads"):
        return SimComplaint(
            complaint_id=f"SIM-PAIR-{arrival_days}",
            arrival=EPOCH + timedelta(days=arrival_days),
            service_days=0.1,
            category=category,
            latitude=lat,
            longitude=lon,
        )

    def test_prefix_counts_equal_production_count_based_ranking(self):
        config = SimConfig(rho=0.8, master_seed=42, max_arrivals=60)
        stream = generate_stream(config, 7)
        counts = snapshot_counts(stream.complaints)
        records = [c.to_record() for c in stream.complaints]
        for position, record in enumerate(records):
            _, prefix_counts = count_based_ranking(records[: position + 1])
            assert counts[record["complaint_id"]] == prefix_counts[record["complaint_id"]]

    def test_mutually_incompatible_records_order_by_production_tie_break(self):
        complaints = [
            SimComplaint(
                complaint_id=f"C{index}",
                arrival=EPOCH + timedelta(days=index * 40.0),
                service_days=0.1,
                category=f"cat-{index % 2}",
                latitude=52.5,
                longitude=13.4,
            )
            for index in range(4)
        ]
        counts = snapshot_counts(complaints)
        assert all(count == 0 for count in counts.values())
        order, production_counts = count_based_ranking(
            [c.to_record() for c in complaints]
        )
        assert production_counts == counts
        expected = sorted(
            complaints, key=lambda c: (-counts[c.complaint_id], c.arrival, c.complaint_id)
        )
        assert order == [c.complaint_id for c in expected]

    def test_distance_and_time_boundaries_match_production_inclusivity(self):
        inside = [self._pair(0.0), self._pair(1.0, *_point_at_meters(52.5, 13.4, 199.5))]
        outside = [self._pair(0.0), self._pair(1.0, *_point_at_meters(52.5, 13.4, 200.5))]
        assert snapshot_counts(inside)[inside[1].complaint_id] == 1
        assert snapshot_counts(outside)[outside[1].complaint_id] == 0
        time_inside = [self._pair(0.0), self._pair(TIME_WINDOW_DAYS)]
        time_outside = [self._pair(0.0), self._pair(TIME_WINDOW_DAYS + 1.0 / 86400.0)]
        assert snapshot_counts(time_inside)[time_inside[1].complaint_id] == 1
        assert snapshot_counts(time_outside)[time_outside[1].complaint_id] == 0
        for pair in (inside, outside, time_inside, time_outside):
            _, production = count_based_ranking([c.to_record() for c in pair])
            assert production[pair[1].complaint_id] == snapshot_counts(pair)[
                pair[1].complaint_id
            ]

    def test_missing_category_or_coordinates_score_zero(self):
        subject = SimComplaint(
            complaint_id="SIM-SUBJECT",
            arrival=EPOCH + timedelta(days=1.0),
            service_days=0.1,
            category="roads",
            latitude=52.5,
            longitude=13.4,
        )
        neighbour = self._pair(0.0)
        no_category = SimComplaint(
            complaint_id="SIM-NO-CAT",
            arrival=EPOCH + timedelta(days=0.5),
            service_days=0.1,
            category=None,
            latitude=52.5,
            longitude=13.4,
        )
        no_coords = SimComplaint(
            complaint_id="SIM-NO-COORDS",
            arrival=EPOCH + timedelta(days=0.5),
            service_days=0.1,
            category="roads",
            latitude=None,
            longitude=None,
        )
        counts = snapshot_counts([neighbour, no_category, no_coords, subject])
        assert counts[subject.complaint_id] == 1
        assert counts[no_category.complaint_id] == 0
        assert counts[no_coords.complaint_id] == 0
        _, production = count_based_ranking(
            [c.to_record() for c in (neighbour, no_category, no_coords, subject)]
        )
        # The full list adds no successors of ``subject`` here, so the
        # production counts coincide with the predecessor-only counts.
        assert production[subject.complaint_id] == 1

    def test_predecessor_only_counts_ignore_mutual_future_evidence(self):
        earlier = self._pair(0.0)
        later = self._pair(1.0)
        counts = snapshot_counts([earlier, later])
        assert counts[earlier.complaint_id] == 0
        assert counts[later.complaint_id] == 1
        _, production = count_based_ranking(
            [earlier.to_record(), later.to_record()]
        )
        # The production helper counts the mutual pair (both directions);
        # the decision-time adapter deliberately does not (design §4.3).
        assert production[earlier.complaint_id] == 1

    def test_conversion_and_candidate_selection_parity_with_harness_oracle(self):
        config = SimConfig(rho=0.8, master_seed=1, max_arrivals=80)
        stream = generate_stream(config, 2)
        records = [c.to_record() for c in stream.complaints]
        for record in records:
            assert to_complaint_input(record) == _harness_to_complaint_input(record)
        pool = records[:20]
        subject = pool[0]
        assert [r["complaint_id"] for r in select_predecessors(subject, pool, 5)] == [
            r["complaint_id"] for r in _harness_select_candidates(subject, pool, 5)
        ]
        # Hand-built pool with a tie on created_at and a missing timestamp.
        tied = [
            {
                "complaint_id": "B",
                "created_at": EPOCH,
                "title": "",
                "description": "",
            },
            {
                "complaint_id": "A",
                "created_at": EPOCH,
                "title": "",
                "description": "",
            },
            {
                "complaint_id": "C",
                "created_at": None,
                "title": "",
                "description": "",
            },
        ]
        assert [r["complaint_id"] for r in select_predecessors(tied[0], tied, 10)] == [
            r["complaint_id"] for r in _harness_select_candidates(tied[0], tied, 10)
        ]

    def test_adapter_never_scores_against_a_successor(self):
        config = SimConfig(rho=0.8, master_seed=1, max_arrivals=40)
        stream = generate_stream(config, 2)
        counts = snapshot_counts(stream.complaints)
        for index, complaint in enumerate(stream.complaints[:20]):
            predecessor_pool = [
                c.to_record()
                for c in stream.complaints[:index]
                if is_recurrence_eligible(c.to_record())
            ]
            _, production = count_based_ranking(
                predecessor_pool + [complaint.to_record()]
            )
            assert counts[complaint.complaint_id] == production[complaint.complaint_id]


class TestPolicyVisibleInformationBoundary:
    """Design §3.1.1/§3.6/§8.4.10: policy inputs carry observable fields only
    — asserted against the snapshots the engine actually passes to policies,
    not only against the declared schema."""

    def test_snapshot_schema_matches_policy_visible_allowlist(self):
        snapshot_names = tuple(f.name for f in dataclass_fields(PolicySnapshot))
        assert snapshot_names == POLICY_VISIBLE_FIELDS
        forbidden = ("sim_", "latent_", "true_", "future_", "generator_")
        assert not any(name.startswith(forbidden) for name in snapshot_names)
        record = SimComplaint(
            complaint_id="A",
            arrival=EPOCH,
            service_days=0.1,
            latent_issue_id="LI-1",
            sim_is_high_priority=True,
        ).to_record()
        assert "latent_issue_id" not in record
        assert "sim_is_high_priority" not in record
        assert not any(key.startswith(forbidden) for key in record)

    def test_policy_execution_receives_only_observable_fields(self, monkeypatch):
        config = SimConfig(
            rho=0.8,
            master_seed=9,
            p_repeat=0.5,
            max_arrivals=80,
            t_warm_days=0.0,
            t_obs_days=60.0,
        )
        stream = generate_stream(config, 4)
        assert any(c.sim_is_high_priority for c in stream.complaints)
        assert all(c.latent_issue_id for c in stream.complaints)
        captured = []

        def spy_key(snapshot):
            captured.append(snapshot)
            return fifo_key(snapshot)

        spy = make_policy(
            name="fifo_spy", key=spy_key, description="snapshot capture"
        )
        monkeypatch.setitem(POLICIES, spy.name, spy)
        result = run_policy(stream, config, spy)
        assert captured
        arrivals = {c.complaint_id: c.arrival for c in stream.complaints}
        for snapshot in captured:
            names = frozenset(vars(snapshot))
            assert names == frozenset(POLICY_VISIBLE_FIELDS)
            assert snapshot.complaint_id in arrivals
            assert snapshot.created_at == arrivals[snapshot.complaint_id]
            assert not hasattr(snapshot, "sim_is_high_priority")
            assert not hasattr(snapshot, "latent_issue_id")
            assert not hasattr(snapshot, "service_days")
        assert {o.complaint_id for o in result.outcomes} == set(arrivals)

    def test_outcome_ordering_is_independent_of_latent_flags(self):
        config = SimConfig(
            rho=0.8,
            master_seed=9,
            p_repeat=0.5,
            max_arrivals=80,
            t_warm_days=0.0,
            t_obs_days=60.0,
        )
        stream = generate_stream(config, 4)
        inverted = ArrivalStream(
            complaints=tuple(
                replace(
                    complaint, sim_is_high_priority=not complaint.sim_is_high_priority
                )
                for complaint in stream.complaints
            ),
            master_seed=stream.master_seed,
            cell_id=stream.cell_id,
            seed=stream.seed,
        )
        base = compute_metrics(run_policy(stream, config, FIFO), config)
        flipped = compute_metrics(run_policy(inverted, config, FIFO), config)
        assert [o.start for o in run_policy(stream, config, FIFO).outcomes] == [
            o.start for o in run_policy(inverted, config, FIFO).outcomes
        ]
        for field in (
            "wait_mean_days",
            "wait_median_days",
            "wait_p95_days",
            "flow_mean_days",
            "completion_rate",
            "fc_start",
            "utilization",
            "backlog_growth_rate",
        ):
            assert getattr(base, field) == getattr(flipped, field)
        assert base.high_n_started != flipped.high_n_started

    def test_future_record_insertion_does_not_change_earlier_outcomes(self):
        config = SimConfig(rho=0.8, master_seed=13, max_arrivals=60)
        stream = generate_stream(config, 2)
        future = SimComplaint(
            complaint_id="SIM-FUTURE-002",
            arrival=stream.complaints[-1].arrival + timedelta(days=0.1),
            service_days=0.05,
        )
        extended = ArrivalStream(
            complaints=tuple(list(stream.complaints) + [future]),
            master_seed=stream.master_seed,
            cell_id=stream.cell_id,
            seed=stream.seed,
        )
        base = run_policy(stream, config, FIFO)
        augmented = run_policy(extended, config, FIFO)
        for complaint in stream.complaints:
            assert base.outcome(complaint.complaint_id) == augmented.outcome(
                complaint.complaint_id
            )


class TestM6HighPriorityWaitingTime:
    """Design §5 M6: M1–M3 restricted to the started cohort members carrying
    the latent designation — a simulated label only (§7.5)."""

    def _stream(self, flags):
        schedule = [(0.0, 1.0), (0.5, 1.0), (0.5, 1.0)]
        complaints = tuple(
            SimComplaint(
                complaint_id=f"H{index}",
                arrival=EPOCH + timedelta(days=arrival),
                service_days=service,
                sim_is_high_priority=flags[index],
            )
            for index, (arrival, service) in enumerate(schedule)
        )
        return ArrivalStream(
            complaints=complaints, master_seed=0, cell_id="", seed=0
        )

    def test_high_priority_subset_metrics_by_hand(self):
        config = SimConfig(
            t_warm_days=0.0, t_obs_days=5.0, t_drain_max_days=10.0
        )
        metrics = compute_metrics(
            run_policy(self._stream([True, False, True]), config, FIFO), config
        )
        assert metrics.cohort_size == 3
        assert metrics.n_started == 3
        assert metrics.wait_mean_days == pytest.approx(2.0 / 3.0, abs=1e-9)
        assert metrics.high_n_started == 2
        assert metrics.high_wait_mean_days == pytest.approx(0.75, abs=1e-9)
        assert metrics.high_wait_median_days == pytest.approx(0.75, abs=1e-9)
        assert metrics.high_wait_p95_days == pytest.approx(1.5, abs=1e-9)

    def test_empty_high_priority_subset_is_none_not_zero(self):
        config = SimConfig(
            t_warm_days=0.0, t_obs_days=5.0, t_drain_max_days=10.0
        )
        metrics = compute_metrics(
            run_policy(self._stream([False, False, False]), config, FIFO), config
        )
        assert metrics.high_n_started == 0
        assert metrics.high_wait_mean_days is None
        assert metrics.high_wait_median_days is None
        assert metrics.high_wait_p95_days is None

    def test_unfinished_high_priority_complaints_are_not_counted(self):
        config = SimConfig(
            t_warm_days=0.0, t_obs_days=5.0, t_drain_max_days=1.0
        )
        complaints = tuple(
            SimComplaint(
                complaint_id=f"H{index}",
                arrival=EPOCH + timedelta(days=arrival),
                service_days=5.0,
                sim_is_high_priority=high,
            )
            for index, (arrival, high) in enumerate(
                [(0.0, True), (0.5, False), (0.5, True)]
            )
        )
        stream = ArrivalStream(
            complaints=complaints, master_seed=0, cell_id="", seed=0
        )
        result = run_policy(stream, config, FIFO)
        metrics = compute_metrics(result, config)
        assert result.outcome("H2").start is None
        assert result.outcome("H1").completion is None
        assert metrics.n_started == 2
        assert metrics.n_completed == 1
        assert metrics.high_n_started == 1
        assert metrics.high_wait_mean_days == pytest.approx(0.0, abs=1e-9)


class TestMm1AnalyticalValidation:
    """Design §6.9: scope-limited engine validation against the textbook
    M/M/1 queue-wait expectation E[W_q] = ρ / (μ − λ) (standard queueing
    result, not a repository claim), targeted at metric M1 (service start −
    arrival, not flow time). 30 independent replications at ρ = 0.8 with
    the configured warm-up/observation/drain windows; the 95% criterion is
    a two-sided Student-t interval over the replication-level means. The
    warm-up spans ≈30 mixing times (t_warm / (1/(μ−λ)) = 30 d / 1 d) for
    this configuration. A pass validates only the FIFO/exponential/c=1/ρ<1
    configuration and the clock arithmetic feeding M1 — nothing more."""

    REPLICATIONS = 30
    # Two-sided Student-t 97.5% quantile at 29 degrees of freedom (textbook
    # constant; scipy is not a project dependency).
    T_CRITICAL = 2.045229642132703

    def test_queue_wait_mean_confidence_interval_covers_analytic_value(self):
        config = SimConfig(
            rho=0.8, master_seed=2026, mean_service_days=0.2,
            service_dist="exponential",
        )
        assert config.c == 1 and config.rho < 1
        mu = 1.0 / config.mean_service_days
        analytic = config.rho / (mu - config.lambda_per_day)
        assert analytic == pytest.approx(0.8)

        estimates = []
        cohort_sizes = []
        for rep in range(self.REPLICATIONS):
            stream = generate_stream(config, rep)
            metrics = compute_metrics(run_policy(stream, config, FIFO), config)
            assert metrics.cohort_size > 0
            assert metrics.fc_start is not None and metrics.fc_start < 0.01
            assert metrics.fc_comp is not None and metrics.fc_comp < 0.01
            assert metrics.wait_mean_days is not None
            estimates.append(metrics.wait_mean_days)
            cohort_sizes.append(metrics.cohort_size)
        # The CI is only informative if each replication contributes a
        # substantial steady-state sample (design §6.9 precondition 4).
        assert min(cohort_sizes) > 100

        estimate = statistics.fmean(estimates)
        half_width = (
            self.T_CRITICAL
            * statistics.stdev(estimates)
            / math.sqrt(self.REPLICATIONS)
        )
        assert estimate - half_width <= analytic <= estimate + half_width


# ---------------------------------------------------------------------------
# Phase D3.3: policies, registry safety, experiment grid, reports, CLI.
# ---------------------------------------------------------------------------

from backend.services.simulation.experiment import (  # noqa: E402
    LOADS,
    CORRELATION_REGIMES,
    DETECTOR_VISIBILITIES,
    POLICY_ORDER,
    SIM_VERSION,
    ExperimentConfig,
    build_evidence,
    enumerate_grid,
    run_experiment,
)
from backend.services.simulation.report import (  # noqa: E402
    report_to_json,
    report_to_markdown,
    write_report,
)


def _policies(config=None):
    from backend.services.simulation.policies import policies_for_config

    return policies_for_config(config or SimConfig())


def _evidence_for(stream, config):
    return build_evidence(stream, config)


class TestPolicyDiscipline:
    """Design §8.4.6: higher key served first; exact tie ⇒ older first."""

    def _config(self):
        return SimConfig(
            rho=0.5, t_warm_days=0.0, t_obs_days=10.0, t_drain_max_days=10.0,
            mean_service_days=1.0, service_dist="constant",
        )

    def _two_waiters_stream(self):
        # A arrives at 0 and occupies the server for 1 day; C then B wait.
        complaints = [
            SimComplaint(
                complaint_id="A-in-service", arrival=EPOCH, service_days=1.0,
                category="roads", latitude=52.5, longitude=13.4,
            ),
            SimComplaint(
                complaint_id="C-older-lowcount", arrival=EPOCH + timedelta(days=0.15),
                service_days=0.1, category="noise", latitude=52.6, longitude=13.5,
            ),
            SimComplaint(
                complaint_id="B-newer-highcount", arrival=EPOCH + timedelta(days=0.2),
                service_days=0.1, category="roads", latitude=52.5, longitude=13.4,
            ),
        ]
        return ArrivalStream(
            complaints=tuple(complaints), master_seed=0, cell_id="", seed=0
        )

    def test_higher_count_key_served_before_older_lower_key(self):
        config = self._config()
        stream = self._two_waiters_stream()
        # B has count 1 (predecessor A: same category, near, within window);
        # C has count 0 (different category). A dispatches at t=1 with both
        # C (older) and B (newer) waiting: count-based must serve B first.
        evidence = PolicyEvidence(
            snapshot_counts={
                "A-in-service": 0,
                "C-older-lowcount": 0,
                "B-newer-highcount": 1,
            },
            d1_evidence_counts={},
        )
        result = run_policy(
            stream, config, _policies(config)["count_based"], evidence=evidence
        )
        assert result.outcome("B-newer-highcount").start == EPOCH + timedelta(days=1.0)
        assert result.outcome("C-older-lowcount").start == EPOCH + timedelta(days=1.1)

    def test_exact_tie_served_oldest_first(self):
        config = self._config()
        stream = self._two_waiters_stream()
        evidence = PolicyEvidence(
            snapshot_counts={
                "A-in-service": 0,
                "C-older-lowcount": 5,
                "B-newer-highcount": 5,
            },
            d1_evidence_counts={},
        )
        result = run_policy(
            stream, config, _policies(config)["count_based"], evidence=evidence
        )
        # Equal counts ⇒ FIFO: C (older waiter) before B.
        assert result.outcome("C-older-lowcount").start == EPOCH + timedelta(days=1.0)
        assert result.outcome("B-newer-highcount").start == EPOCH + timedelta(days=1.1)


class TestEvidenceCap:
    """Design §4.4: recurrence_evidence = min(n, CAP) / CAP, CAP config-owned."""

    def _snapshot(self, d1_count):
        # Same id/created_at for every count so the key's score component
        # is the only thing that can differ.
        return PolicySnapshot(
            complaint_id="S", created_at=EPOCH, d1_evidence_count=d1_count
        )

    def test_saturates_at_cap(self):
        policies = _policies(SimConfig(evidence_cap=3.0))
        key = policies["recurrence_aware"].key
        assert key(self._snapshot(3))[0] == key(self._snapshot(10))[0]
        assert key(self._snapshot(3))[0] != key(self._snapshot(1))[0]

    def test_cap_from_config_travels_with_policy(self):
        policies = _policies(SimConfig(evidence_cap=1.0))
        policy = policies["recurrence_aware"]
        assert policy.evidence_cap == 1.0
        # With CAP=1 any single relation saturates.
        assert policy.key(self._snapshot(1))[0] == policy.key(self._snapshot(7))[0]

    def test_zero_relations_matches_rule_based_score(self):
        policies = _policies(SimConfig())
        assert (
            policies["recurrence_aware"].key(self._snapshot(0))[0]
            == policies["rule_based"].key(
                PolicySnapshot(complaint_id="S", created_at=EPOCH)
            )[0]
        )

    def test_cap_must_be_positive(self):
        with pytest.raises(ValueError, match="CAP"):
            from backend.services.simulation.policies import make_recurrence_aware_key

            make_recurrence_aware_key(0.0)


class TestRegistrySafety:
    """Approved-plan §3: factory-stamped instances accepted; forgeries rejected."""

    def test_hand_built_policy_with_registered_name_rejected(self):
        config = SimConfig()
        stream = _manual_stream([0.1])
        forged = Policy(name="fifo", key=fifo_key)
        with pytest.raises(ValueError, match="approved factory"):
            run_policy(stream, config, forged)

    def test_validate_policy_rejects_forgery_and_unknown(self):
        validate_policy(FIFO)  # registered factory instance: accepted
        with pytest.raises(ValueError, match="unknown policy"):
            validate_policy(Policy(name="nope", key=fifo_key))
        with pytest.raises(ValueError, match="approved factory"):
            validate_policy(Policy(name="fifo", key=fifo_key))

    def test_factory_instance_with_registered_name_accepted(self):
        config = SimConfig()
        stream = _manual_stream([0.0, 0.5], service_days=0.4)
        replacement_fifo = make_policy(name="fifo", key=fifo_key, description="copy")
        import backend.services.simulation.policies as policies_module

        original = policies_module.POLICIES["fifo"]
        policies_module.POLICIES["fifo"] = replacement_fifo
        try:
            run_policy(stream, config, replacement_fifo)
        finally:
            policies_module.POLICIES["fifo"] = original

    def test_policies_for_config_instances_accepted(self):
        config = SimConfig(evidence_cap=2.0)
        stream = generate_stream(
            SimConfig(rho=0.5, master_seed=3, max_arrivals=30, t_warm_days=0.0,
                      t_obs_days=40.0, t_drain_max_days=20.0),
            0,
        )
        bound = policies_for_config(config)
        evidence = _evidence_for(stream, config)
        for name in ("fifo", "rule_based", "count_based", "recurrence_aware"):
            run_policy(stream, config, bound[name], evidence=evidence)

    def test_evidence_based_policies_require_evidence(self):
        config = SimConfig()
        stream = _manual_stream([0.1])
        bound = policies_for_config(config)
        for name in ("count_based", "recurrence_aware"):
            with pytest.raises(ValueError, match="requires the frozen"):
                run_policy(stream, config, bound[name], evidence=None)

    def test_registry_contains_exactly_the_four_non_oracle_policies(self):
        assert set(POLICIES) == {
            "fifo", "rule_based", "count_based", "recurrence_aware"
        }
        assert not any(name.startswith("oracle_") for name in POLICIES)


class TestDetectorBlindInvariance:
    """Design §8.4.11 / §6.3.8: recurrence_aware ≡ rule_based when blind."""

    def test_identical_outcomes_and_metrics(self):
        config = SimConfig(
            rho=0.8, master_seed=11, p_repeat=0.5, max_arrivals=70,
            t_warm_days=0.0, t_obs_days=60.0, t_drain_max_days=30.0,
            detector_visibility="detector_blind",
        )
        stream = generate_stream(config, 5)
        evidence = _evidence_for(stream, config)
        # Detector-blind: the D1 adapter produces no relations at all.
        assert set(evidence.d1_evidence_counts.values()) == {0}
        bound = policies_for_config(config)
        rule_result = run_policy(stream, config, bound["rule_based"], evidence=evidence)
        aware_result = run_policy(
            stream, config, bound["recurrence_aware"], evidence=evidence
        )
        # Same starts/completions for every complaint (the RunResult
        # wrapper's `policy` label legitimately differs by name).
        assert rule_result.outcomes == aware_result.outcomes
        assert compute_metrics(rule_result, config) == compute_metrics(
            aware_result, config
        )


class TestMissingDataPolicies:
    """Design §8.4.7 at the policy layer: missing fields sink, never drop."""

    def test_missing_category_and_coords_score_zero_keys(self):
        policies = _policies(SimConfig())
        empty = PolicySnapshot(complaint_id="M-1", created_at=EPOCH)
        assert policies["count_based"].key(empty)[0] == 0.0
        assert (
            policies["recurrence_aware"].key(empty)[0]
            == policies["rule_based"].key(empty)[0]
        )

    def test_rule_based_inherits_production_uncertainty_behaviour(self):
        # The production scorer flags missing severity/location as
        # uncertainty and never silently down-weights; the simulation
        # policy calls it with severity_label=None (absent by design).
        policies = _policies(SimConfig())
        snapshot = PolicySnapshot(
            complaint_id="M-2", created_at=EPOCH, category=None,
            latitude=None, longitude=None,
        )
        result_key = policies["rule_based"].key(snapshot)
        assert result_key[1:] == (EPOCH, "M-2")


class TestExperimentGrid:
    def _tiny_config(self, **kwargs):
        defaults = dict(
            sim=SimConfig(
                rho=0.8, master_seed=5, replications=2, max_arrivals=40,
                t_warm_days=0.0, t_obs_days=40.0, t_drain_max_days=20.0,
            ),
            loads=(0.8,),
            correlation_regimes=("independent",),
            detector_visibilities=("detector_visible",),
            # The §6.9 validation block is included in every report; the grid
            # tests use a reduced replication count so the suite stays fast.
            # The validation module itself is exercised separately in
            # TestMm1Validation.
            mm1_replications=3,
        )
        defaults.update(kwargs)
        return ExperimentConfig(**defaults)

    def test_grid_enumeration_order_and_ids(self):
        config = ExperimentConfig(
            sim=SimConfig(replications=1),
            loads=LOADS,
            correlation_regimes=CORRELATION_REGIMES,
            detector_visibilities=DETECTOR_VISIBILITIES,
        )
        cells = enumerate_grid(config)
        assert len(cells) == 3 * 3 * 2
        assert [c.index for c in cells] == list(range(18))
        assert cells[0].cell_id == "rho=0.5|regime=correlated|visibility=detector_visible"
        assert cells[-1].cell_id == "rho=1.2|regime=anti_correlated|visibility=detector_blind"
        # Canonical design §6.1 order: loads × regimes × visibility.
        assert POLICY_ORDER == ("fifo", "rule_based", "count_based", "recurrence_aware")

    def test_run_experiment_records_seeds_checksums_and_censoring(self):
        report = run_experiment(self._tiny_config())
        cell = report["cells"][0]
        assert len(cell["seeds"]) == 2
        for seed_record in cell["seeds"]:
            assert isinstance(seed_record["stream_checksum"], str)
            assert len(seed_record["stream_checksum"]) == 64
            # The replication index is recorded under an unambiguous name and
            # the *actual* derived per-stream seeds are recorded alongside it.
            assert seed_record["rep_index"] == seed_record["rep"]
            assert "stream_seed" not in seed_record
            derived = seed_record["derived_stream_seeds"]
            assert set(derived) == {
                "arrivals",
                "service",
                "attrs",
                "recurrence",
                "evidence",
                "missing",
            }
            for name, value in derived.items():
                assert value == derive_stream_seed(
                    report["config"]["sim"]["master_seed"],
                    cell["cell"]["cell_id"],
                    seed_record["rep"],
                    name,
                )
        fifo_per_seed = cell["policies"]["fifo"]["per_seed"]
        for metrics in fifo_per_seed.values():
            assert "fc_start" in metrics and "fc_comp" in metrics
            assert "cohort_size" in metrics
        assert report["sim_version"] == SIM_VERSION

    def test_crn_shared_stream_checksum_across_reps(self):
        report = run_experiment(self._tiny_config())
        seeds = report["cells"][0]["seeds"]
        # Distinct reps ⇒ distinct derived streams (different checksums);
        # within a rep all policies share one stream by construction.
        assert seeds[0]["stream_checksum"] != seeds[1]["stream_checksum"]

    def test_policy_order_independence(self):
        from backend.services.simulation.experiment import _metrics_for_policy_order

        cell_config = self._tiny_config()
        cell = enumerate_grid(cell_config)[0]
        forward = {}
        reverse = _metrics_for_policy_order(
            cell, 0, cell_config.sim, POLICY_ORDER, tuple(reversed(POLICY_ORDER))
        )
        forward = _metrics_for_policy_order(
            cell, 0, cell_config.sim, POLICY_ORDER, POLICY_ORDER
        )
        assert forward == reverse

    def test_run_experiment_is_deterministic(self):
        first = run_experiment(self._tiny_config())
        second = run_experiment(self._tiny_config())
        assert first == second

    def test_paired_contrast_math_by_hand(self):
        report = run_experiment(self._tiny_config())
        contrasts = report["cells"][0]["paired_contrasts"]
        pair = contrasts["rule_based_minus_fifo"]
        summary = pair["wait_mean_days"]
        # Hand-recompute the per-seed differences from the raw records.
        per_seed = report["cells"][0]["policies"]
        differences = [
            per_seed["rule_based"]["per_seed"][rep]["wait_mean_days"]
            - per_seed["fifo"]["per_seed"][rep]["wait_mean_days"]
            for rep in ("0", "1")
        ]
        assert summary["mean"] == pytest.approx(sum(differences) / len(differences))
        assert summary["n_seeds"] == 2
        assert summary["interpretation"].startswith("Monte-Carlo error")

    def test_resolved_flag_excludes_zero_rule(self):
        report = run_experiment(self._tiny_config())
        pair = report["cells"][0]["paired_contrasts"]["rule_based_minus_fifo"]
        for metric_name, summary in pair.items():
            if summary["n_seeds"] == 0:
                assert summary["resolved_excludes_zero"] is False
                continue
            expected = summary["p2_5"] > 0.0 or summary["p97_5"] < 0.0
            assert summary["resolved_excludes_zero"] is expected

    def test_no_latent_truth_in_policy_inputs(self):
        # The engine snapshot allowlist test (§8.4.10) covers isolation;
        # here we assert the experiment report carries no latent values as
        # *data* — the only permitted occurrences of latent names are the
        # aggregate §3.7 diagnostics and the M6 metric-definition text.
        import json

        report = run_experiment(self._tiny_config())
        parsed = json.loads(report_to_json(report))

        def walk_keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from walk_keys(value)
            elif isinstance(node, list):
                for item in node:
                    yield from walk_keys(item)

        forbidden = (
            "latent_issue_id", "sim_is_high_priority", "sim_service_time",
            "sim_arrival_index", "future_outcome",
        )
        assert not any(key in forbidden for key in walk_keys(parsed))
        assert "realized_p_high" in report_to_json(report)  # §3.7 aggregates


class TestReportRendering:
    def _report(self):
        return run_experiment(
            ExperimentConfig(
                sim=SimConfig(
                    rho=0.8, master_seed=7, replications=1, max_arrivals=30,
                    t_warm_days=0.0, t_obs_days=30.0, t_drain_max_days=15.0,
                ),
                loads=(0.8,),
                correlation_regimes=("independent",),
                detector_visibilities=("detector_blind",),
            )
        )

    def test_banner_is_first_line_and_json_round_trips(self):
        report = self._report()
        markdown = report_to_markdown(report)
        assert markdown.splitlines()[0] == (
            "# SIMULATED DATA — NOT REAL-WORLD EVIDENCE"
        )
        import json

        assert json.loads(report_to_json(report)) == report

    def test_markdown_contains_formulas_labels_and_not_evaluated(self):
        markdown = report_to_markdown(report_to_json and self._report())
        assert "W̄_q" in markdown  # M1 formula
        assert "f_c^{start}" in markdown  # censoring rule
        assert "Primary (predeclared" in markdown
        assert "Secondary/exploratory" in markdown
        assert "not_evaluated" in markdown
        assert "exact_source" in markdown
        assert "necessary, not\nsufficient" in markdown or "necessary, not" in markdown
        assert "not run (deferred)" in markdown
        assert "Monte-Carlo error of the simulation estimator only" in markdown
        assert "real-world effectiveness" in markdown

    def test_config_echo_matches_live_production_constants(self):
        report = self._report()
        echoed = report["config"]["sim"]["production_constants"]
        assert echoed["SIMILARITY_THRESHOLD"] == SIMILARITY_THRESHOLD
        assert echoed["LOCATION_METERS_MAX"] == LOCATION_METERS_MAX
        assert echoed["TIME_WINDOW_DAYS"] == TIME_WINDOW_DAYS
        assert echoed["REQUIRED_SIGNALS"] == sorted(REQUIRED_SIGNALS)

    def test_write_report_round_trip(self, tmp_path):
        report = self._report()
        json_path, md_path = write_report(report, tmp_path, stem="sim_test")
        assert json_path.exists() and md_path.exists()
        import json

        reloaded = json.loads(json_path.read_text(encoding="utf-8"))
        assert reloaded == report
        assert md_path.read_text(encoding="utf-8").startswith(
            "# SIMULATED DATA"
        )


class TestCli:
    def test_no_write_prints_json_report(self, capsys):
        from scripts.run_simulation import main

        exit_code = main(
            [
                "--no-write",
                "--replications", "1",
                "--load", "0.8",
                "--scenario", "independent",
                "--visibility", "detector_blind",
                "--max-arrivals", "20",
                "--t-warm", "0",
                "--t-obs", "20",
                "--t-drain", "10",
            ]
        )
        assert exit_code == 0
        import json

        payload = json.loads(capsys.readouterr().out)
        assert payload["banner"] == "SIMULATED DATA — NOT REAL-WORLD EVIDENCE"
        assert payload["config"]["experiment"]["replications"] == 1
        assert payload["config"]["experiment"]["grid_cell_count"] == 1
        assert payload["provenance"]["status"] in (
            "reproducible_from_commit",
            "incomplete",
        )
        assert payload["mm1_validation"]["analytic_target"]


class TestAuditRemediation:
    """Phase D3.3 audit remediation: provenance, censoring, M/M/1, backlog
    curve, empty-cohort handling, fairness checks (audit remediation)."""

    # ------------------------------------------------------------------
    # Phase 4 — conditional censoring warning
    # ------------------------------------------------------------------
    def test_censoring_warning_threshold_is_conditional(self):
        from backend.services.simulation.metrics import (
            CENSORING_WARNING_TEXT,
            CENSORING_WARN_THRESHOLD,
            censoring_warning,
        )

        assert CENSORING_WARN_THRESHOLD == 0.1
        assert CENSORING_WARNING_TEXT == (
            "censored subset — steady-state interpretation invalid"
        )
        # At or below the threshold: no stamp.
        assert censoring_warning(0.0) is None
        assert censoring_warning(0.05) is None
        assert censoring_warning(0.1) is None
        # Above the threshold: the stamp.
        assert censoring_warning(0.1000001) == CENSORING_WARNING_TEXT
        assert censoring_warning(1.0) == CENSORING_WARNING_TEXT
        # Undefined (empty cohort): never a stamp, never a false claim.
        assert censoring_warning(None) is None

    def _censoring_report(self):
        """A run whose cohort is heavily censored (rho=1.2, 1-day drain)."""
        config = ExperimentConfig(
            sim=SimConfig(
                rho=1.2,
                master_seed=11,
                replications=2,
                t_warm_days=10.0,
                t_obs_days=60.0,
                t_drain_max_days=1.0,
            ),
            loads=(1.2,),
            correlation_regimes=("independent",),
            detector_visibilities=("detector_visible",),
            mm1_replications=2,
        )
        return run_experiment(config)

    def test_censoring_stamp_applied_when_threshold_exceeded(self):
        report = self._censoring_report()
        cell = report["cells"][0]
        assert cell["censoring"]["threshold"] == 0.1
        triggered = report["censoring"]["triggered"]
        assert triggered, "expected at least one censored (cell, policy, rep)"
        for entry in triggered:
            assert entry["fc_start"] > 0.1
            assert entry["warning"] == (
                "censored subset — steady-state interpretation invalid"
            )
        # The stamp is attached to the wait metrics themselves.
        for policy in cell["policies"].values():
            summary = policy["across_seeds"]["wait_mean_days"]
            assert summary["censoring_warning"] == (
                "censored subset — steady-state interpretation invalid"
            )
            assert summary["censoring_warning_reps"]

    def test_censoring_stamp_absent_when_below_threshold(self):
        config = ExperimentConfig(
            sim=SimConfig(
                rho=0.5,
                master_seed=3,
                replications=2,
                t_warm_days=0.0,
                t_obs_days=40.0,
                t_drain_max_days=30.0,
            ),
            loads=(0.5,),
            correlation_regimes=("independent",),
            detector_visibilities=("detector_visible",),
            mm1_replications=2,
        )
        report = run_experiment(config)
        assert report["censoring"]["triggered"] == []
        summary = report["cells"][0]["policies"]["fifo"]["across_seeds"][
            "wait_mean_days"
        ]
        assert summary["censoring_warning"] is None

    def test_censoring_stamp_visible_in_json_and_markdown(self):
        import json

        report = self._censoring_report()
        markdown = report_to_markdown(report)
        assert "censored subset — steady-state interpretation invalid" in markdown
        assert "## Censoring (conditional warning" in markdown
        # JSON is the source of truth and carries the same stamp.
        payload = json.loads(report_to_json(report))
        assert payload["censoring"]["warning_text"] == (
            "censored subset — steady-state interpretation invalid"
        )
        assert payload["censoring"]["triggered_count"] >= 1

    def test_empty_cohort_is_not_evaluated_and_never_stamped(self):
        config = ExperimentConfig(
            sim=SimConfig(
                rho=0.8,
                master_seed=7,
                replications=2,
                max_arrivals=5,
                t_warm_days=30.0,
                t_obs_days=40.0,
                t_drain_max_days=10.0,
            ),
            loads=(0.8,),
            correlation_regimes=("independent",),
            detector_visibilities=("detector_visible",),
            mm1_replications=2,
        )
        report = run_experiment(config)
        cell = report["cells"][0]
        assert cell["cohort_status"] == "no_evaluation_cohort"
        assert cell["cohort_status_label"] == (
            "No evaluation cohort — outcomes not evaluated"
        )
        assert report["censoring"]["triggered"] == []
        assert report["censoring"]["not_evaluated_count"] > 0
        for policy in cell["policies"].values():
            assert policy["outcome_status"] == "not_evaluated"
            assert policy["across_seeds"]["wait_mean_days"][
                "censoring_warning"
            ] is None

    # ------------------------------------------------------------------
    # Phase 5 — M/M/1 validation block
    # ------------------------------------------------------------------
    def test_mm1_validation_block_is_complete(self):
        from backend.services.simulation.validation import run_mm1_validation

        config = SimConfig(
            rho=0.8,
            master_seed=13,
            t_warm_days=10.0,
            t_obs_days=60.0,
            t_drain_max_days=20.0,
        )
        block = run_mm1_validation(config, replications=6)
        assert block["replications"] == 6
        assert block["estimate_queue_wait_mean_days"] is not None
        interval = block["confidence_interval_95_days"]
        assert interval["low"] < interval["high"]
        assert interval["degrees_of_freedom"] == 5
        assert interval["t_critical_975"] == pytest.approx(2.570582, rel=1e-6)
        # Analytic value: rho = 0.8, mu = 1/0.2 = 5, lambda = rho*mu = 4.
        assert block["analytic_queue_wait_mean_days"] == pytest.approx(0.8)
        assert block["analytic_time_in_system_days"] == pytest.approx(1.0)
        assert block["relaxation_time_days"] == pytest.approx(1.0)
        assert block["warmup_over_relaxation_ratio"] == pytest.approx(10.0)
        assert block["criterion"]["reported_regardless_of_pass_or_fail"] is True
        assert "covers_analytic_value" in block["criterion"]
        assert block["limitations"]
        assert "necessary, not sufficient" in block["limitations"][0]

    def test_mm1_no_closed_form_outside_its_domain(self):
        from backend.services.simulation.validation import (
            analytic_queue_wait_mean,
        )

        # rho < 1: the closed form applies.
        assert analytic_queue_wait_mean(0.8, 5.0, 4.0) == pytest.approx(0.8)
        # rho >= 1: no steady state, so no value is produced at all.
        assert analytic_queue_wait_mean(1.0, 5.0, 5.0) is None
        assert analytic_queue_wait_mean(1.2, 5.0, 6.0) is None
        # Not an M/M/1 configuration: no value either.
        assert analytic_queue_wait_mean(0.8, 5.0, 4.0, servers=2) is None
        assert (
            analytic_queue_wait_mean(0.8, 5.0, 4.0, service_dist="uniform")
            is None
        )

    def test_mm1_t_critical_table(self):
        from backend.services.simulation.validation import t_critical_975

        assert t_critical_975(29) == pytest.approx(2.045230, rel=1e-6)
        assert t_critical_975(30) == pytest.approx(2.042272, rel=1e-6)
        assert t_critical_975(1) == pytest.approx(12.706205, rel=1e-6)
        # Beyond the table the nearest smaller df is used (conservative).
        assert t_critical_975(1000) == pytest.approx(1.979930, rel=1e-6)
        with pytest.raises(ValueError):
            t_critical_975(0)

    def test_mm1_block_present_in_report_and_markdown(self):
        report = self._censoring_report()
        assert "mm1_validation" in report
        markdown = report_to_markdown(report)
        assert "## M/M/1 engine validation" in markdown
        assert "Analytic E[W_q] = ρ/(μ−λ)" in markdown
        assert "Time in system" in markdown or "time in system" in markdown

    # ------------------------------------------------------------------
    # Phase 6 — backlog curve in the Markdown
    # ------------------------------------------------------------------
    def test_backlog_curve_is_rendered(self):
        config = ExperimentConfig(
            sim=SimConfig(
                rho=0.8,
                master_seed=17,
                replications=2,
                t_warm_days=0.0,
                t_obs_days=40.0,
                t_drain_max_days=20.0,
            ),
            loads=(0.8,),
            correlation_regimes=("independent",),
            detector_visibilities=("detector_visible",),
            mm1_replications=2,
        )
        report = run_experiment(config)
        markdown = report_to_markdown(report)
        assert "Backlog curve B(t)" in markdown
        assert "service starts" in markdown
        # The complete daily series stays in the JSON.
        series = report["cells"][0]["policies"]["fifo"]["per_seed"]["0"][
            "backlog_series"
        ]
        assert len(series) >= 2
        assert all(len(point) == 2 for point in series)

    # ------------------------------------------------------------------
    # Phase 7 — empty / invalid cohorts
    # ------------------------------------------------------------------
    def _empty_cohort_report(self):
        config = ExperimentConfig(
            sim=SimConfig(
                rho=0.8,
                master_seed=19,
                replications=2,
                max_arrivals=5,
                t_warm_days=30.0,
                t_obs_days=40.0,
                t_drain_max_days=10.0,
            ),
            loads=(0.8,),
            correlation_regimes=("independent",),
            detector_visibilities=("detector_visible",),
            mm1_replications=2,
        )
        return run_experiment(config)

    def test_empty_cohort_metrics_are_null_not_warmup_values(self):
        report = self._empty_cohort_report()
        cell = report["cells"][0]
        for policy in cell["policies"].values():
            across = policy["across_seeds"]
            # The evaluation summary must not present warm-up state as an
            # evaluation-period outcome.
            assert "utilization" not in across
            assert "backlog_growth_rate" not in across
            diagnostics = policy["no_cohort_diagnostics"]["metrics"]
            assert "utilization" in diagnostics
            assert "backlog_growth_rate" in diagnostics
            assert "NOT" in policy["no_cohort_diagnostics"]["note"]
            # Every outcome summary is empty.
            assert across["wait_mean_days"]["n_seeds"] == 0
            assert across["wait_mean_days"]["mean"] is None

    def test_empty_cohort_contrasts_are_not_evidence(self):
        report = self._empty_cohort_report()
        contrasts = report["cells"][0]["paired_contrasts"]
        assert contrasts
        for pair, per_metric in contrasts.items():
            for metric_name, summary in per_metric.items():
                assert summary["status"] == "not_evaluated"
                assert summary["evidence_for_either_policy"] is False
                assert summary["status_reason"]

    def test_empty_cohort_markdown_labels_the_cell(self):
        report = self._empty_cohort_report()
        markdown = report_to_markdown(report)
        assert "No evaluation cohort — outcomes not evaluated" in markdown
        assert "not an outcome" in markdown

    # ------------------------------------------------------------------
    # Phase 2 — provenance
    # ------------------------------------------------------------------
    def test_provenance_block_describes_the_actual_source_state(self):
        from backend.services.simulation.provenance import build_provenance

        provenance = build_provenance()
        assert provenance["status"] in (
            "reproducible_from_commit",
            "incomplete",
        )
        assert provenance["head"]["commit"]
        assert provenance["source_digest"]["combined_sha256"]
        assert provenance["source_digest"]["file_count"] > 0
        # The status follows the working tree, it is not a constant.
        clean = provenance["working_tree"]["clean"]
        assert provenance["status"] == (
            "reproducible_from_commit" if clean else "incomplete"
        )
        assert provenance["reproducible_from_recorded_commit"] is clean

    def test_provenance_is_rendered_in_markdown(self):
        report = self._censoring_report()
        report["provenance"] = {
            "status": "incomplete",
            "reproducible_from_recorded_commit": False,
            "head": {
                "commit": "abc1234",
                "commit_full": "abc1234def",
                "commit_date": "2026-10-21T00:00:00+00:00",
                "branch": "main",
            },
            "working_tree": {
                "clean": False,
                "modified_tracked": ["backend/services/recurrence/service.py"],
                "staged": [],
                "untracked": ["backend/services/simulation/experiment.py"],
                "diff_head_sha256": "f" * 64,
            },
            "uncommitted_paths": [
                "backend/services/recurrence/service.py",
                "backend/services/simulation/experiment.py",
            ],
            "source_digest": {
                "algorithm": "sha256",
                "file_count": 3,
                "combined_sha256": "a" * 64,
                "files": {},
            },
            "notes": [],
        }
        markdown = report_to_markdown(report)
        assert "## Provenance and reproducibility" in markdown
        assert "Status: `incomplete`" in markdown
        assert "does **not** reproduce this" in markdown
        assert "abc1234" in markdown
        assert "a" * 64 in markdown

    # ------------------------------------------------------------------
    # Fairness checklist — actual checks, not constants
    # ------------------------------------------------------------------
    def test_fairness_checklist_records_actual_checks(self):
        config = ExperimentConfig(
            sim=SimConfig(
                rho=0.8,
                master_seed=23,
                replications=2,
                t_warm_days=0.0,
                t_obs_days=40.0,
                t_drain_max_days=20.0,
            ),
            loads=(0.8,),
            correlation_regimes=("independent",),
            detector_visibilities=("detector_visible",),
            mm1_replications=2,
        )
        report = run_experiment(config)
        checklist = report["fairness_checklist"]
        expected = len(report["cells"]) * config.sim.replications
        for key in (
            "identical_streams_and_service_times_per_rep",
            "identical_policy_visible_snapshots",
            "identical_eligibility_conventions",
            "identical_evidence_snapshot_mode",
            "identical_windows_and_t_cut",
            "identical_metrics_implementation",
            "compute_cost_excluded_from_metrics",
        ):
            entry = checklist[key]
            assert isinstance(entry, dict)
            assert entry["passed"] is True
            assert entry["checks_performed"] == expected
            assert entry["checks_passed"] == expected
            assert entry["examples"]
            assert entry["examples"][0]["detail"]
        assert checklist["policy_order_independence"]["checks_performed"] == len(
            report["cells"]
        )
        assert checklist["policy_order_independence"]["passed"] is True


class TestContrastEvidenceQualification:
    """Audit item 10: a small-R pilot's contrasts are not statistical evidence.

    Design §6.6 defines the paired-contrast interval as the empirical
    2.5/97.5 percentile range of the per-seed paired differences. Below
    ``MIN_REPLICATIONS_FOR_CONTRAST_INTERVAL`` replications that range
    degenerates to the sample minimum/maximum, so a "resolved" flag must
    not be presented as evidence that the policies differ.
    """

    def _tiny_config(self, **kwargs):
        defaults = dict(
            sim=SimConfig(
                rho=0.8, master_seed=5, replications=2, max_arrivals=40,
                t_warm_days=0.0, t_obs_days=40.0, t_drain_max_days=20.0,
            ),
            loads=(0.8,),
            correlation_regimes=("independent",),
            detector_visibilities=("detector_visible",),
            mm1_replications=3,
        )
        defaults.update(kwargs)
        return ExperimentConfig(**defaults)

    def _metrics(self, wait_mean, cohort=10):
        from backend.services.simulation.metrics import Metrics

        return Metrics(
            cohort_size=cohort,
            n_started=cohort,
            n_completed=cohort,
            wait_mean_days=wait_mean,
            wait_median_days=wait_mean,
            wait_p95_days=wait_mean,
            flow_mean_days=None,
            flow_median_days=None,
            flow_p95_days=None,
            completion_rate=1.0,
            fc_start=0.0,
            fc_comp=0.0,
            backlog_series=((0.0, 0),),
            backlog_growth_rate=0.0,
            utilization=0.5,
            high_n_started=0,
            high_wait_mean_days=None,
            high_wait_median_days=None,
            high_wait_p95_days=None,
        )

    def _contrasts(self, n_reps):
        from backend.services.simulation.experiment import _paired_contrasts

        metrics_by_rep = {
            rep: {
                "fifo": self._metrics(1.0),
                "rule_based": self._metrics(1.0 + 0.1 * (rep + 1)),
            }
            for rep in range(n_reps)
        }
        return _paired_contrasts(metrics_by_rep, ("fifo", "rule_based"))

    def test_pilot_scale_contrasts_are_not_evidence(self):
        from backend.services.simulation.experiment import (
            MIN_REPLICATIONS_FOR_CONTRAST_INTERVAL,
        )

        contrasts = self._contrasts(2)
        entry = contrasts["rule_based_minus_fifo"]["wait_mean_days"]
        assert entry["n_seeds"] == 2
        assert entry["replications_used"] == 2
        assert entry["evidence_strength"] == "insufficient_replications"
        assert entry["evidence_for_either_policy"] is False
        assert entry["caveat"]
        assert "not statistical evidence" in entry["caveat"]
        # The design's own rule is untouched: the interval still excludes 0.
        assert entry["resolved_excludes_zero"] is True
        assert MIN_REPLICATIONS_FOR_CONTRAST_INTERVAL == 5

    def test_enough_replications_make_the_contrast_readable(self):
        contrasts = self._contrasts(6)
        entry = contrasts["rule_based_minus_fifo"]["wait_mean_days"]
        assert entry["n_seeds"] == 6
        assert entry["evidence_strength"] == "sufficient_replications"
        assert entry["evidence_for_either_policy"] is True
        assert entry["caveat"] is None

    def test_report_level_block_states_the_threshold(self):
        from backend.services.simulation.experiment import (
            MIN_REPLICATIONS_FOR_CONTRAST_INTERVAL,
            _contrast_evidence_block,
        )

        small = _contrast_evidence_block(2)
        assert small["informative"] is False
        assert small["replications_used"] == 2
        assert (
            small["min_replications_for_informative_interval"]
            == MIN_REPLICATIONS_FOR_CONTRAST_INTERVAL
        )
        assert small["note"]
        large = _contrast_evidence_block(30)
        assert large["informative"] is True
        assert large["note"] is None

    def test_pilot_report_marks_the_contrasts_inconclusive(self):
        report = run_experiment(self._tiny_config())
        assert report["contrast_evidence"]["informative"] is False
        assert report["contrast_evidence"]["replications_used"] == 2
        assert any(
            "not statistical evidence" in limitation
            for limitation in report["limitations"]
        )
        markdown = report_to_markdown(report)
        assert "## Paired-contrast evidence" in markdown
        assert "Informative: **no**" in markdown
        cell = report["cells"][0]
        pair = cell["paired_contrasts"]["rule_based_minus_fifo"]
        entry = pair["wait_mean_days"]
        if entry["n_seeds"]:
            assert entry["evidence_strength"] == "insufficient_replications"
            assert "not statistical evidence" in entry["caveat"]
            assert entry["caveat"] in markdown


class TestRegistryAndCapConsistency:
    """Audit: registry validation and evidence-cap consistency (design §4.4)."""

    def test_mislabelled_policy_is_rejected(self):
        from backend.services.simulation.policies import rule_based_key

        mislabelled = make_policy(name="fifo", key=rule_based_key)
        with pytest.raises(ValueError, match="registered under that name"):
            validate_policy(mislabelled)

    def test_mislabelled_policy_is_rejected_by_the_engine(self):
        from backend.services.simulation.policies import rule_based_key

        config = SimConfig()
        stream = _manual_stream([0.1])
        mislabelled = make_policy(name="fifo", key=rule_based_key)
        with pytest.raises(ValueError, match="registered under that name"):
            run_policy(stream, config, mislabelled)

    def test_recurrence_aware_without_a_cap_is_rejected(self):
        from backend.services.simulation.policies import make_recurrence_aware_key

        config = SimConfig()
        stream = _manual_stream([0.1])
        uncapped = make_policy(
            name="recurrence_aware",
            key=make_recurrence_aware_key(3.0),
            evidence_cap=None,
        )
        with pytest.raises(ValueError, match="CAP"):
            validate_policy(uncapped)
        with pytest.raises(ValueError, match="CAP"):
            run_policy(stream, config, uncapped)

    def test_recurrence_aware_cap_must_match_the_run_config(self):
        config = SimConfig(evidence_cap=5.0)
        stream = _manual_stream([0.1])
        bound = policies_for_config(config)
        assert bound["recurrence_aware"].evidence_cap == 5.0
        # A policy built for a different CAP than the run config is a
        # silent measurement error: it must be rejected.
        other = policies_for_config(SimConfig(evidence_cap=3.0))
        with pytest.raises(ValueError, match="evidence_cap"):
            run_policy(stream, config, other["recurrence_aware"])

    def test_recurrence_aware_key_and_policy_must_agree_on_the_cap(self):
        from backend.services.simulation.policies import make_recurrence_aware_key

        key = make_recurrence_aware_key(3.0)
        mismatched = make_policy(
            name="recurrence_aware", key=key, evidence_cap=5.0
        )
        with pytest.raises(ValueError, match="CAP"):
            validate_policy(mismatched)


class TestProvenanceLabelling:
    """Audit item 1: the report must not claim reproducibility it lacks."""

    def _tiny_config(self, **kwargs):
        defaults = dict(
            sim=SimConfig(
                rho=0.8, master_seed=5, replications=2, max_arrivals=40,
                t_warm_days=0.0, t_obs_days=40.0, t_drain_max_days=20.0,
            ),
            loads=(0.8,),
            correlation_regimes=("independent",),
            detector_visibilities=("detector_visible",),
            mm1_replications=3,
        )
        defaults.update(kwargs)
        return ExperimentConfig(**defaults)

    def _report(self):
        report = run_experiment(self._tiny_config())
        report["provenance"] = {
            "status": "incomplete",
            "reproducible_from_recorded_commit": False,
            "head": {"commit": "abc1234", "commit_full": "abc1234def",
                     "commit_date": "2026-10-21T00:00:00+00:00", "branch": "main"},
            "working_tree": {
                "clean": False,
                "modified_tracked": ["backend/services/recurrence/service.py"],
                "staged": [],
                "untracked": ["backend/services/simulation/experiment.py"],
                "diff_head_sha256": "f" * 64,
            },
            "uncommitted_paths": ["backend/services/recurrence/service.py"],
            "source_digest": {
                "algorithm": "sha256",
                "file_count": 3,
                "combined_sha256": "a" * 64,
                "files": {},
            },
            "notes": [],
        }
        return report

    def test_header_does_not_claim_reproducibility_from_a_dirty_tree(self):
        markdown = report_to_markdown(self._report())
        header = markdown.splitlines()[:8]
        joined = "\n".join(header)
        assert "Last committed revision (HEAD)" in joined
        assert "does **not** reproduce this report" in joined

    def test_header_states_reproducibility_when_the_tree_was_clean(self):
        report = self._report()
        report["provenance"]["status"] = "reproducible_from_commit"
        report["provenance"]["reproducible_from_recorded_commit"] = True
        report["provenance"]["working_tree"]["clean"] = True
        markdown = report_to_markdown(report)
        joined = "\n".join(markdown.splitlines()[:8])
        assert "reproduces the report" in joined
        assert "does **not** reproduce this report" not in joined


# ---------------------------------------------------------------------------
# Severity-label propagation regression tests (severity-propagation change).
# Covers: SimComplaint → PolicySnapshot survival; rule-based scorer receives
# the observable label; missing-severity remains supported; FIFO ordering is
# unaffected by severity changes.
# ---------------------------------------------------------------------------


class TestSeverityPropagation:
    """Regression tests for the severity-label propagation added in the
    severity-propagation working-tree change.

    The four tests exercise the data flow:
      SimComplaint.severity_label
        → _snapshot_of()
          → PolicySnapshot.severity_label
            → _score() / rule_based_key()
              → RuleBasedPrioritizer.score()

    No severity labels are invented to favour a policy outcome: the tests
    only verify the plumbing (label survival, scorer receipt, None tolerance,
    FIFO independence). They do not assert that any policy outperforms another.
    """

    # ------------------------------------------------------------------
    # Test 1: severity_label survives SimComplaint → PolicySnapshot.
    # ------------------------------------------------------------------

    def test_severity_label_carried_from_simcomplaint_to_policysnapshot(self):
        """A severity_label on SimComplaint appears unchanged on the snapshot."""
        complaint = SimComplaint(
            complaint_id="SEV-001",
            arrival=EPOCH,
            service_days=0.1,
            severity_label="high",
        )
        snapshot = _snapshot_of(complaint)
        assert snapshot.severity_label == "high"

    def test_none_severity_label_carried_from_simcomplaint_to_policysnapshot(self):
        """severity_label=None is faithfully propagated (no substitution)."""
        complaint = SimComplaint(
            complaint_id="SEV-002",
            arrival=EPOCH,
            service_days=0.1,
            severity_label=None,
        )
        snapshot = _snapshot_of(complaint)
        assert snapshot.severity_label is None

    # ------------------------------------------------------------------
    # Test 2: the rule-based policy key receives the observable severity label.
    # ------------------------------------------------------------------

    def test_rule_based_key_receives_observable_severity_label(self):
        """rule_based_key() produces a higher score for a recognised severity
        label than for None.  This does not assert superiority over another
        policy — it verifies that the scorer's severity branch is reachable
        from the snapshot, which is what the propagation change enables.
        """
        snapshot_with = PolicySnapshot(
            complaint_id="SEV-WITH",
            created_at=EPOCH,
            severity_label="high",
        )
        snapshot_without = PolicySnapshot(
            complaint_id="SEV-WITHOUT",
            created_at=EPOCH,
            severity_label=None,
        )
        score_with, _, _ = rule_based_key(snapshot_with)
        score_without, _, _ = rule_based_key(snapshot_without)
        # A recognised "high" label contributes sev=0.9 with weight 0.40
        # vs sev=0.0 for None; scores must differ (not testing exact values).
        assert score_with > score_without

    # ------------------------------------------------------------------
    # Test 3: missing severity (None) continues to be supported.
    # ------------------------------------------------------------------

    def test_rule_based_key_tolerates_missing_severity_label(self):
        """rule_based_key() must not raise when severity_label is None."""
        snapshot = PolicySnapshot(
            complaint_id="SEV-NONE",
            created_at=EPOCH,
            severity_label=None,
        )
        score, tie_dt, cid = rule_based_key(snapshot)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0
        assert tie_dt == EPOCH
        assert cid == "SEV-NONE"

    # ------------------------------------------------------------------
    # Test 4: severity changes do NOT alter FIFO ordering.
    # ------------------------------------------------------------------

    def test_fifo_ordering_unchanged_by_severity_label(self):
        """Changing severity_label on a complaint must not affect FIFO dispatch
        order, because FIFO ignores all attributes other than created_at and
        complaint_id.
        """
        config = SimConfig(
            t_warm_days=0.0, t_obs_days=5.0, t_drain_max_days=5.0
        )
        # Three complaints arriving at the same time so that queue ordering
        # is determined purely by FIFO tie-break (created_at then id).
        complaints_base = tuple(
            SimComplaint(
                complaint_id=f"SEV-FIFO-{i}",
                arrival=EPOCH,
                service_days=0.5,
                severity_label=None,
            )
            for i in range(3)
        )
        complaints_sev = tuple(
            replace(c, severity_label="critical") for c in complaints_base
        )
        stream_base = ArrivalStream(
            complaints=complaints_base, master_seed=0, cell_id="", seed=0
        )
        stream_sev = ArrivalStream(
            complaints=complaints_sev, master_seed=0, cell_id="", seed=0
        )
        result_base = run_policy(stream_base, config, FIFO)
        result_sev = run_policy(stream_sev, config, FIFO)
        # Service start times must be identical under both label sets.
        for complaint in complaints_base:
            assert result_base.outcome(complaint.complaint_id).start == (
                result_sev.outcome(complaint.complaint_id).start
            ), (
                f"{complaint.complaint_id}: FIFO start changed when severity "
                "label changed — severity must not influence FIFO ordering"
            )
