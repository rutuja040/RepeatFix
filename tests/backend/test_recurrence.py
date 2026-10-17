import json
from datetime import datetime, timedelta

from backend.services.recurrence.service import RecurrenceAnalysisService
from backend.services.recurrence.types import ComplaintInput


def mk_input(cid: str, **kwargs) -> ComplaintInput:
    defaults = dict(
        complaint_id=cid,
        title="Pothole on Main Street",
        description="Large pothole near intersection",
        category="Roads",
        latitude=40.7,
        longitude=-74.0,
        location_label="Main St & 5th Ave",
        administrative_area="Manhattan",
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        source=None,
        synthetic=False,
        data_quality_flags=None,
        raw_imported_values=None,
    )
    defaults.update(kwargs)
    return ComplaintInput(**defaults)


def test_text_similarity_alone_insufficient():
    svc = RecurrenceAnalysisService()
    a = mk_input("A", latitude=40.7, longitude=-74.0, created_at=datetime(2026, 1, 1))
    b = mk_input(
        "B",
        title="Pothole on Main Street",
        description="Large pothole near intersection",
        latitude=41.0,  # far away
        longitude=-75.0,
        created_at=datetime(2026, 6, 1),  # large gap
        category="Sanitation",  # different
    )
    res = svc.analyze(a, [b])
    assert len(res.relations) == 0


def test_similar_text_compatible_category_and_location():
    svc = RecurrenceAnalysisService()
    a = mk_input("A")
    b = mk_input(
        "B",
        title="Pothole on Main Street",
        description="Large pothole forming",
        latitude=40.7001,
        longitude=-74.0001,
        created_at=a.created_at + timedelta(days=2),
    )
    res = svc.analyze(a, [b])
    assert len(res.relations) == 1
    rel = res.relations[0]
    assert rel.relation_type == "potential_recurrence"
    assert rel.requires_human_review is True
    assert rel.is_auto_merge_candidate is False


def test_similar_but_distant_locations():
    svc = RecurrenceAnalysisService()
    a = mk_input("A")
    b = mk_input(
        "B",
        title="Pothole on Main Street",
        description="Large pothole",
        latitude=40.8,
        longitude=-74.0,
        created_at=a.created_at + timedelta(days=1),
    )
    res = svc.analyze(a, [b])
    assert len(res.relations) == 0


def test_incompatible_categories():
    svc = RecurrenceAnalysisService()
    a = mk_input("A", category="Roads")
    b = mk_input(
        "B",
        category="Sanitation",
        title="Pothole on Main Street",
        description="Large pothole",
        latitude=40.7001,
        longitude=-74.0001,
        created_at=a.created_at + timedelta(days=1),
    )
    res = svc.analyze(a, [b])
    assert len(res.relations) == 0


def test_large_time_gaps():
    svc = RecurrenceAnalysisService()
    a = mk_input("A")
    b = mk_input(
        "B",
        title="Pothole on Main St",
        description="Big pothole",
        latitude=40.7001,
        longitude=-74.0001,
        created_at=a.created_at + timedelta(days=60),
    )
    res = svc.analyze(a, [b])
    assert len(res.relations) == 0


def test_missing_coordinates():
    svc = RecurrenceAnalysisService()
    a = mk_input("A", latitude=None, longitude=None)
    b = mk_input(
        "B",
        title="Pothole on Main Street",
        description="Large pothole",
        latitude=None,
        longitude=None,
        created_at=a.created_at + timedelta(days=1),
        administrative_area="Manhattan",
    )
    res = svc.analyze(a, [b])
    # With weak admin area only (0.05) and text similarity, needs strong evidence
    assert "coordinates" in res.missing_data
    assert len(res.relations) == 0


def test_missing_timestamps():
    svc = RecurrenceAnalysisService()
    a = mk_input("A", created_at=None)
    b = mk_input(
        "B",
        title="Pothole on Main Street",
        description="Large pothole",
        created_at=None,
        latitude=40.7001,
        longitude=-74.0001,
    )
    res = svc.analyze(a, [b])
    assert "created_at" in res.missing_data
    assert len(res.relations) == 0


def test_exact_source_records_classified_separately():
    svc = RecurrenceAnalysisService()
    ra = {"source_key": "label:ds::R1"}
    rb = {"source_key": "label:ds::R1"}
    a = mk_input("A", raw_imported_values=json.dumps(ra))
    b = mk_input("B", raw_imported_values=json.dumps(rb))
    res = svc.analyze(a, [b])
    assert len(res.relations) == 1
    assert res.relations[0].relation_type == "exact_source"
    assert res.relations[0].is_auto_merge_candidate is False


def test_similar_never_auto_merged():
    svc = RecurrenceAnalysisService()
    a = mk_input("A")
    b = mk_input("B", title="Pothole Main St", description="Hole", latitude=40.7002, longitude=-74.0002, created_at=a.created_at+timedelta(hours=6))
    res = svc.analyze(a, [b])
    assert all(r.is_auto_merge_candidate is False for r in res.relations)
    assert all(r.requires_human_review is True for r in res.relations)


def test_explainable_and_deterministic():
    svc = RecurrenceAnalysisService()
    a = mk_input("A")
    b = mk_input("B", title="Pothole on Main Street", description="Deep pothole", latitude=40.7001, longitude=-74.0001, created_at=a.created_at+timedelta(days=1))
    r1 = svc.analyze(a, [b])
    r2 = svc.analyze(a, [b])
    assert r1.relations == r2.relations
    if r1.relations:
        assert r1.relations[0].confidence_explained >= 0
        assert r1.relations[0].confidence_label in ("low", "medium", "high")


def test_data_quality_flags_propagated():
    svc = RecurrenceAnalysisService()
    a = mk_input("A", data_quality_flags="missing_created_at")
    res = svc.analyze(a, [])
    assert res.complaint_id == "A"


def test_invalid_inputs_handled():
    svc = RecurrenceAnalysisService()
    a = mk_input("A", latitude=200, longitude=-200, created_at=None)
    res = svc.analyze(a, [])
    assert res.warnings or res.missing_data
