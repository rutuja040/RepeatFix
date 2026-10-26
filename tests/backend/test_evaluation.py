"""Tests for the Phase D2.1 label-free evaluation harness and temporal as-of."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from backend.services.evaluation.baselines import (
    count_based_ranking,
    fifo_ranking,
    rule_based_ranking,
)
from backend.services.evaluation.harness import (
    EvaluationConfig,
    _select_candidates,
    run_evaluation,
)
from backend.services.evaluation.reporting import report_to_json, report_to_markdown
from backend.services.recurrence.service import REQUIRED_SIGNALS, RecurrenceAnalysisService
from backend.services.recurrence.types import (
    LOCATION_METERS_MAX,
    SIMILARITY_THRESHOLD,
    TIME_WINDOW_DAYS,
    ComplaintInput,
)

T0 = datetime(2026, 1, 1, 12, 0, 0)


def _mk_input(cid: str, **kwargs) -> ComplaintInput:
    defaults = dict(
        complaint_id=cid,
        title="Pothole on Main Street",
        description="Large pothole near intersection",
        category="Roads",
        latitude=40.7,
        longitude=-74.0,
        location_label="Main St",
        administrative_area="Manhattan",
        created_at=T0,
        source=None,
        synthetic=False,
        data_quality_flags=None,
        raw_imported_values=None,
    )
    defaults.update(kwargs)
    return ComplaintInput(**defaults)


def _mk_complaint(db, cid, created_at, **kwargs):
    from backend.models.complaint import Complaint

    fields = dict(
        complaint_id=cid,
        title=kwargs.pop("title", "Pothole on Main Street"),
        description=kwargs.pop("description", "Large pothole near intersection"),
        category=kwargs.pop("category", "Roads"),
        latitude=kwargs.pop("latitude", 40.7),
        longitude=kwargs.pop("longitude", -74.0),
        created_at=created_at,
        synthetic=kwargs.pop("synthetic", False),
    )
    fields.update(kwargs)
    row = Complaint(**fields)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _session():
    from backend.db.session import SessionLocal

    return SessionLocal()


# --------------------------------------------------------------------------- #
# Component 1: temporal as-of evaluation
# --------------------------------------------------------------------------- #


def test_service_cutoff_excludes_future_candidates():
    svc = RecurrenceAnalysisService()
    a = _mk_input("A", created_at=T0)
    b = _mk_input("B", created_at=T0 + timedelta(days=2))

    assert len(svc.analyze(a, [b]).relations) == 1

    cutoff = T0 + timedelta(hours=1)
    assert len(svc.analyze(a, [b], cutoff=cutoff).relations) == 0


def test_service_cutoff_skips_candidates_without_timestamp():
    svc = RecurrenceAnalysisService()
    a = _mk_input("A", created_at=T0)
    b = _mk_input("B", created_at=None)

    assert len(svc.analyze(a, [b]).relations) == 0
    assert len(svc.analyze(a, [b], cutoff=T0 + timedelta(days=1)).relations) == 0


def test_service_default_behaviour_unchanged():
    svc = RecurrenceAnalysisService()
    a = _mk_input("A")
    b = _mk_input("B", created_at=T0 + timedelta(days=2))

    baseline = svc.analyze(a, [b])
    explicit = svc.analyze(a, [b], cutoff=None)
    assert baseline.relations == explicit.relations


def test_production_api_exposes_no_signal_ablation_parameter():
    """The public production API cannot weaken the Phase D1 decision rule."""
    import inspect

    parameters = inspect.signature(RecurrenceAnalysisService.analyze).parameters
    assert "disabled_signals" not in parameters
    assert set(parameters) == {"self", "complaint", "candidates", "cutoff"}


def test_ablation_is_evaluation_side_only():
    """Ablation narrows the required signals through the protected hook."""
    from backend.services.evaluation.harness import _AblationRecurrenceService

    a = _mk_input("A")
    b = _mk_input(
        "B",
        title="Rooftop water leak on Broadway",
        description="Ceiling drip in the apartment above the bakery",
        created_at=T0 + timedelta(days=2),
    )

    production = RecurrenceAnalysisService()
    ablated = _AblationRecurrenceService({"text_similarity"})

    assert production._required_signals() == REQUIRED_SIGNALS
    assert ablated._required_signals() == REQUIRED_SIGNALS - {"text_similarity"}
    # Without the text signal the production rule rejects the pair ...
    assert len(production.analyze(a, [b]).relations) == 0
    # ... while the evaluation-only ablation accepts it.
    assert len(ablated.analyze(a, [b]).relations) == 1


def test_run_evaluation_cutoff_excludes_future(client):
    db = _session()
    try:
        _mk_complaint(db, "A", T0)
        _mk_complaint(db, "B", T0 + timedelta(days=5))

        before = run_evaluation(db, EvaluationConfig(cutoff=T0 + timedelta(hours=1)))
        assert before["counts"]["ranking_eligible"] == 1
        assert before["counts"]["excluded"]["after_cutoff"] == 1
        for baseline in before["baselines"]:
            assert "B" not in baseline["ranking"]

        after = run_evaluation(db, EvaluationConfig(cutoff=T0 + timedelta(days=6)))
        assert after["counts"]["ranking_eligible"] == 2
        assert after["counts"]["excluded"]["after_cutoff"] == 0
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Component 2: deterministic baselines
# --------------------------------------------------------------------------- #


def test_rule_based_baseline_ignores_resolved_at():
    records = [
        {
            "complaint_id": "A",
            "category": "Roads",
            "created_at": T0,
            "latitude": 40.7,
            "longitude": -74.0,
            "status": "resolved",
            "severity_label": None,
            "resolved_at": None,
        },
        {
            "complaint_id": "B",
            "category": "Roads",
            "created_at": T0 + timedelta(days=1),
            "latitude": 40.7001,
            "longitude": -74.0001,
            "status": "resolved",
            "severity_label": None,
            "resolved_at": T0 + timedelta(days=3),
        },
    ]
    with_resolution = rule_based_ranking(records)[1]

    for record in records:
        record["resolved_at"] = None
    without_resolution = rule_based_ranking(records)[1]

    assert with_resolution == without_resolution


def test_run_evaluation_is_deterministic(client):
    db = _session()
    try:
        _mk_complaint(db, "A", T0)
        _mk_complaint(db, "B", T0 + timedelta(days=1))
        config = EvaluationConfig(run_timestamp=T0, code_commit="test")
        first = run_evaluation(db, config)
        second = run_evaluation(db, config)
        assert report_to_json(first) == report_to_json(second)
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Component 3: recurrence signal ablation
# --------------------------------------------------------------------------- #


def test_run_evaluation_ablation_and_agreement(client):
    db = _session()
    try:
        _mk_complaint(db, "A", T0)
        # B: identical text, same category, nearby, recent -> full relation with A.
        _mk_complaint(db, "B", T0 + timedelta(days=1), latitude=40.7001, longitude=-74.0001)
        # C: identical to A but far away -> only related when proximity is disabled.
        _mk_complaint(db, "C", T0 + timedelta(days=1), latitude=41.5, longitude=-75.5)
        # D: near + recent + same category but dissimilar text.
        _mk_complaint(
            db,
            "D",
            T0 + timedelta(days=1),
            title="Loud night noise complaint",
            description="Repeated late-night noise from venue",
            latitude=40.7001,
            longitude=-74.0001,
        )

        report = run_evaluation(db, EvaluationConfig(run_timestamp=T0))
        assert report["recurrence_ablation"]["eligible_count"] == 4

        configs = {c["name"]: c for c in report["recurrence_ablation"]["configs"]}
        assert set(configs) == {
            "full",
            "without_text_similarity",
            "without_same_category",
            "without_nearby",
            "without_time_proximity",
        }
        assert configs["without_nearby"]["relations_total"] > configs["full"]["relations_total"]
        assert configs["without_text_similarity"]["relations_total"] > configs["full"]["relations_total"]

        agreement = report["recurrence_ablation"]["agreement"]
        assert agreement["vs_full"]["without_nearby"]["only_in_a"] >= 1
        assert "not accuracy" in agreement["note"].lower()
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Component 4: report output
# --------------------------------------------------------------------------- #


def test_report_structure_and_no_fabricated_metrics(client):
    db = _session()
    try:
        _mk_complaint(db, "A", T0)
        report = run_evaluation(db, EvaluationConfig(run_timestamp=T0))

        for key in (
            "metadata",
            "snapshot",
            "config",
            "counts",
            "missing_data",
            "baselines",
            "recurrence_ablation",
            "not_evaluated",
        ):
            assert key in report

        assert report["not_evaluated"]
        for baseline in report["baselines"]:
            assert baseline["top_k"]
            for info in baseline["performance_metrics"].values():
                assert info["status"] == "not_evaluated"
                assert info["reason"]

        # JSON must round-trip.
        assert json.loads(report_to_json(report))["metadata"]["harness_version"] == "d2.1"
    finally:
        db.close()


def test_missing_data_rates_and_recurrence_eligibility(client):
    db = _session()
    try:
        _mk_complaint(db, "A", T0)
        _mk_complaint(db, "B", T0, latitude=None, longitude=None)
        _mk_complaint(db, "C", T0, category=None)

        report = run_evaluation(db, EvaluationConfig(run_timestamp=T0))
        assert report["counts"]["ranking_eligible"] == 3
        # Only A has coordinates + category.
        assert report["counts"]["recurrence_eligible"] == 1
        rates = report["missing_data"]["ranking_eligible"]
        assert rates["latitude"] > 0
        assert rates["category"] > 0
    finally:
        db.close()


def test_markdown_report_renders_sections(client):
    db = _session()
    try:
        _mk_complaint(db, "A", T0)
        report = run_evaluation(db, EvaluationConfig(run_timestamp=T0))
        md = report_to_markdown(report)
        assert "# RepeatFix Evaluation Report" in md
        assert "## Recurrence signal ablation" in md
        assert "## Metrics not evaluated" in md
        assert "not_evaluated" in md  # label-dependent metrics surfaced as not evaluated
    finally:
        db.close()


def test_synthetic_excluded_by_default(client):
    db = _session()
    try:
        _mk_complaint(db, "A", T0)
        _mk_complaint(db, "S", T0, synthetic=True)

        default = run_evaluation(db, EvaluationConfig(run_timestamp=T0))
        assert default["counts"]["ranking_eligible"] == 1
        assert default["counts"]["excluded"]["synthetic"] == 1

        included = run_evaluation(
            db, EvaluationConfig(run_timestamp=T0, include_synthetic=True)
        )
        assert included["counts"]["ranking_eligible"] == 2
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Regression tests (Phase D2.1 audit)
# --------------------------------------------------------------------------- #


def test_service_cutoff_includes_candidate_exactly_at_cutoff():
    svc = RecurrenceAnalysisService()
    a = _mk_input("A", created_at=T0)
    b = _mk_input("B", created_at=T0)

    # created_at == cutoff is "as of t0" and must be visible.
    result = svc.analyze(a, [b], cutoff=T0)
    assert len(result.relations) == 1
    assert result.relations[0].related_complaint_id == "B"

    # A millisecond after the cutoff is future information.
    result = svc.analyze(a, [b], cutoff=T0 - timedelta(microseconds=1))
    assert len(result.relations) == 0


def test_run_evaluation_cutoff_boundary(client):
    db = _session()
    try:
        _mk_complaint(db, "A", T0)
        _mk_complaint(db, "B", T0)

        report = run_evaluation(db, EvaluationConfig(cutoff=T0, run_timestamp=T0))
        assert report["counts"]["ranking_eligible"] == 2
        assert report["counts"]["excluded"]["after_cutoff"] == 0
    finally:
        db.close()


def test_run_evaluation_empty_dataset(client):
    db = _session()
    try:
        report = run_evaluation(db, EvaluationConfig(run_timestamp=T0))
        assert report["counts"]["ranking_eligible"] == 0
        assert report["counts"]["recurrence_eligible"] == 0
        assert report["snapshot"]["total_complaints"] == 0
        for baseline in report["baselines"]:
            assert baseline["ranking"] == []
            assert baseline["top_k"] == []

        configs = report["recurrence_ablation"]["configs"]
        assert all(c["complaints_analyzed"] == 0 for c in configs)
        agreement = report["recurrence_ablation"]["agreement"]
        for info in agreement["vs_full"].values():
            assert info["jaccard"] is None
            assert info["jaccard_status"] == "undefined_both_sets_empty"
        for row in agreement["matrix_jaccard"].values():
            assert all(value is None for value in row.values())

        # Must still render without crashing.
        assert report_to_markdown(report)
    finally:
        db.close()


def test_agreement_undefined_for_two_empty_relation_sets():
    from backend.services.evaluation.harness import _agreement

    info = _agreement(set(), set())
    assert info["shared"] == 0
    assert info["jaccard"] is None
    assert info["jaccard_status"] == "undefined_both_sets_empty"

    # Any non-empty union is a real measurement again.
    measured = _agreement({("A", "B")}, set())
    assert measured["jaccard"] == 0.0
    assert measured["jaccard_status"] == "computed"


def test_rankings_break_ties_by_complaint_id():
    same_time = [
        {"complaint_id": "C3", "category": "Roads", "created_at": T0, "latitude": 40.7, "longitude": -74.0, "status": "open", "severity_label": None, "resolved_at": None},
        {"complaint_id": "C1", "category": "Roads", "created_at": T0, "latitude": 40.7, "longitude": -74.0, "status": "open", "severity_label": None, "resolved_at": None},
        {"complaint_id": "C2", "category": "Roads", "created_at": T0, "latitude": 40.7, "longitude": -74.0, "status": "open", "severity_label": None, "resolved_at": None},
    ]

    assert fifo_ranking(same_time) == ["C1", "C2", "C3"]

    count_order, counts = count_based_ranking(same_time)
    assert count_order == ["C1", "C2", "C3"]
    assert counts == {"C3": 2, "C1": 2, "C2": 2}

    rule_order, scores = rule_based_ranking(same_time)
    assert rule_order == ["C1", "C2", "C3"]
    assert len(set(scores.values())) == 1  # identical inputs -> identical scores

    # Candidate selection is also id-ordered when timestamps are equal.
    selected = _select_candidates(same_time[0], same_time, max_candidates=10)
    assert [r["complaint_id"] for r in selected] == ["C1", "C2"]


def test_count_based_ranking_missing_coordinates_or_category():
    records = [
        # Full data: counted once (pairs with D only; B has no coordinates,
        # C has no category).
        {"complaint_id": "A", "category": "Roads", "created_at": T0, "latitude": 40.7, "longitude": -74.0, "status": "open", "severity_label": None, "resolved_at": None},
        # Missing coordinates: never counted, receives 0.
        {"complaint_id": "B", "category": "Roads", "created_at": T0, "latitude": None, "longitude": None, "status": "open", "severity_label": None, "resolved_at": None},
        # Missing category: never counted, receives 0.
        {"complaint_id": "C", "category": None, "created_at": T0, "latitude": 40.7, "longitude": -74.0, "status": "open", "severity_label": None, "resolved_at": None},
        # Full data: counted once (pairs with A only; C has no category).
        {"complaint_id": "D", "category": "Roads", "created_at": T0, "latitude": 40.7001, "longitude": -74.0001, "status": "open", "severity_label": None, "resolved_at": None},
    ]
    order, counts = count_based_ranking(records)
    assert counts["A"] == 1
    assert counts["D"] == 1
    assert counts["B"] == 0
    assert counts["C"] == 0
    # Zero-count records still appear, ordered by FIFO tie-break.
    assert order == ["A", "D", "B", "C"]


def test_fifo_and_count_rankings_deterministic_on_missing_time():
    records = [
        {"complaint_id": "Z", "category": "Roads", "created_at": None, "latitude": None, "longitude": None, "status": "open", "severity_label": None, "resolved_at": None},
        {"complaint_id": "A", "category": "Roads", "created_at": T0, "latitude": 40.7, "longitude": -74.0, "status": "open", "severity_label": None, "resolved_at": None},
    ]
    # Both baselines sort missing timestamps as the epoch sentinel, ahead of
    # any real timestamp; ties fall back to complaint_id.
    assert fifo_ranking(records) == ["Z", "A"]
    order, counts = count_based_ranking(records)
    assert order == ["Z", "A"]
    assert counts == {"Z": 0, "A": 0}


def test_select_candidates_orders_most_recent_first_and_skips_subject():
    pool = [
        {"complaint_id": "A", "created_at": T0},
        {"complaint_id": "B", "created_at": T0 + timedelta(days=2)},
        {"complaint_id": "C", "created_at": T0 + timedelta(days=1)},
        {"complaint_id": "D", "created_at": None},
    ]
    selected = _select_candidates(pool[0], pool, max_candidates=10)
    # Most recent first; subject excluded; missing timestamp last.
    assert [r["complaint_id"] for r in selected] == ["B", "C", "D"]

    capped = _select_candidates(pool[0], pool, max_candidates=1)
    assert [r["complaint_id"] for r in capped] == ["B"]


def test_select_candidates_tz_independent_ordering():
    # Naive-UTC values must sort identically regardless of the host's local
    # timezone (regression: .timestamp() interpreted naive datetimes locally).
    from zoneinfo import ZoneInfo

    aware = [
        {"complaint_id": "A", "created_at": T0.replace(tzinfo=ZoneInfo("UTC"))},
        {"complaint_id": "B", "created_at": (T0 + timedelta(hours=1)).replace(tzinfo=ZoneInfo("UTC"))},
    ]
    naive = [
        {"complaint_id": "A", "created_at": T0},
        {"complaint_id": "B", "created_at": T0 + timedelta(hours=1)},
    ]
    assert [r["complaint_id"] for r in _select_candidates(naive[0], naive, 10)] == ["B"]
    assert [r["complaint_id"] for r in _select_candidates(aware[0], aware, 10)] == ["B"]


def test_exact_source_relation_at_cutoff():
    svc = RecurrenceAnalysisService()
    a = _mk_input("A", created_at=T0, raw_imported_values='{"source_key": "ds1::row-9"}')
    b = _mk_input(
        "B",
        created_at=T0,
        source="ds1",
        raw_imported_values='{"source_key": "ds1::row-9", "source_record_id": "row-9"}',
    )

    # Candidate exactly at the cutoff is visible -> exact_source relation.
    result = svc.analyze(a, [b], cutoff=T0)
    assert len(result.relations) == 1
    assert result.relations[0].relation_type == "exact_source"

    # After the cutoff the candidate is future information.
    result = svc.analyze(a, [b], cutoff=T0 - timedelta(seconds=1))
    assert len(result.relations) == 0


def test_report_records_reproducibility_thresholds(client):
    db = _session()
    try:
        report = run_evaluation(db, EvaluationConfig(run_timestamp=T0))
        config = report["config"]
        assert config["recurrence_thresholds"] == {
            "similarity_threshold": SIMILARITY_THRESHOLD,
            "location_meters_max": LOCATION_METERS_MAX,
            "time_window_days": TIME_WINDOW_DAYS,
        }
        assert config["count_based_thresholds"] == {
            "meters": LOCATION_METERS_MAX,
            "days": TIME_WINDOW_DAYS,
        }
        assert config["required_recurrence_signals"] == sorted(REQUIRED_SIGNALS)
        md = report_to_markdown(report)
        assert "Recurrence thresholds" in md
        assert "Count-based thresholds" in md
    finally:
        db.close()
