"""Tests for priority scoring integration in complaint creation and CSV import.

Verifies:
- complaint creation persists priority_score and priority_rationale.
- imported complaints have scores persisted on accepted rows.
- missing optional fields produce the scorer's documented uncertainty flags.
- the persisted score matches the production scorer for identical inputs.
- existing duplicate detection and import validation continue to work.
"""

import io
import json

import pytest

from backend.services.prioritization.rule_based import (
    PrioritizationInputs,
    RuleBasedPrioritizer,
)

BASE = "/api/v1"
COLLECTION = f"{BASE}/complaints"
CSV_URL = f"{BASE}/imports/csv"


# ------------------------------------------------------------------ helpers


def make_complaint(client, cid, **extra):
    payload = {"complaint_id": cid, "title": "Test complaint"}
    payload.update(extra)
    r = client.post(COLLECTION, json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def get_complaint(client, cid):
    r = client.get(f"{COLLECTION}/{cid}")
    assert r.status_code == 200, r.text
    return r.json()


def upload_csv(client, content, *, source_label="scoring-test", **kwargs):
    payload = content.encode("utf-8") if isinstance(content, str) else content
    files = {"file": ("test.csv", io.BytesIO(payload), "text/csv")}
    data = {"source_label": source_label}
    data.update(kwargs)
    r = client.post(CSV_URL, files=files, data=data)
    assert r.status_code == 200, r.text
    return r.json()


def simple_csv(
    cid="SC-1",
    subject="Pothole on Main St",
    ctype="Roads",
    lat="40.7",
    lon="-74.0",
    severity="",
    status="open",
):
    header = "request_id,subject,complaint_type,lat,lon,severity_label,status\n"
    row = f"{cid},{subject},{ctype},{lat},{lon},{severity},{status}\n"
    return header + row


# ------------------------------------------------------------------ complaint creation


def test_create_persists_priority_score(client):
    """A newly created complaint must have a non-null priority_score."""
    body = make_complaint(client, "PS-1", severity_label="high",
                          latitude=40.7, longitude=-74.0, category="Roads",
                          description="Large pothole")
    assert body["priority_score"] is not None
    assert isinstance(body["priority_score"], float)
    assert 0.0 <= body["priority_score"] <= 1.0


def test_create_persists_priority_rationale(client):
    """A newly created complaint must have a non-null priority_rationale."""
    body = make_complaint(client, "PR-1", severity_label="medium")
    assert body["priority_rationale"] is not None
    rationale = json.loads(body["priority_rationale"])
    assert "rationale" in rationale
    assert "components" in rationale
    assert "uncertainty_flags" in rationale
    assert "missing_fields" in rationale


def test_create_score_matches_production_scorer(client):
    """The persisted score must equal the production scorer output for the same inputs."""
    body = make_complaint(
        client,
        "PM-1",
        severity_label="critical",
        latitude=51.5,
        longitude=-0.1,
        category="Structural",
        description="Building collapse risk",
        location_label="High Street",
    )
    assert body["priority_score"] is not None

    completeness_fields = ("description", "category", "severity_label", "location_label")
    present = sum(1 for f in completeness_fields if body.get(f) not in (None, ""))
    comp = round(present / len(completeness_fields), 4)

    inputs = PrioritizationInputs(
        complaint_id=body["complaint_id"],
        category=body["category"],
        severity_label=body["severity_label"],
        latitude=body["latitude"],
        longitude=body["longitude"],
        evidence_completeness=comp,
    )
    expected = RuleBasedPrioritizer().score(inputs).priority_score
    assert body["priority_score"] == expected


def test_create_missing_severity_produces_uncertainty_flag(client):
    """Missing severity_label must produce missing_severity uncertainty flag."""
    body = make_complaint(client, "MU-1")
    assert body["priority_score"] is not None
    rationale = json.loads(body["priority_rationale"])
    assert "missing_severity" in rationale["uncertainty_flags"]
    assert "severity_label" in rationale["missing_fields"]


def test_create_missing_location_produces_uncertainty_flag(client):
    """Missing lat/lon must produce missing_location uncertainty flag."""
    body = make_complaint(client, "ML-1", severity_label="high")
    rationale = json.loads(body["priority_rationale"])
    assert "missing_location" in rationale["uncertainty_flags"]
    assert "location" in rationale["missing_fields"]


def test_create_no_severity_no_location_manual_review(client):
    """No severity + no location + low completeness triggers manual_review_recommended."""
    body = make_complaint(client, "MR-1")
    rationale = json.loads(body["priority_rationale"])
    assert "manual_review_recommended" in rationale["uncertainty_flags"]


def test_create_high_severity_scores_higher_than_low(client):
    """High-severity+location must score higher than low-severity+no-location."""
    high = make_complaint(
        client, "HIGH-1",
        severity_label="critical", latitude=40.7, longitude=-74.0,
        category="Safety", description="Bridge crack"
    )
    low = make_complaint(client, "LOW-1", severity_label="low")
    assert high["priority_score"] > low["priority_score"]


def test_create_score_is_deterministic(client):
    """Two identical complaints must produce the same priority_score."""
    kwargs = dict(severity_label="medium", category="Roads", latitude=40.7, longitude=-74.0)
    a = make_complaint(client, "DET-A", **kwargs)
    b = make_complaint(client, "DET-B", **kwargs)
    assert a["priority_score"] == b["priority_score"]


def test_create_score_bounded_0_1(client):
    """Priority score must always be in [0, 1]."""
    body = make_complaint(
        client, "BD-1",
        severity_label="critical", latitude=40.7, longitude=-74.0,
        category="Emergency", description="Gas leak", location_label="Main St"
    )
    assert 0.0 <= body["priority_score"] <= 1.0


def test_create_does_not_break_existing_roundtrip(client):
    """Scoring integration must not break the basic complaint roundtrip."""
    body = make_complaint(client, "RT-1", title="Streetlight out", category="Lighting")
    assert body["complaint_id"] == "RT-1"
    assert body["title"] == "Streetlight out"
    assert body["status"] == "open"
    assert body["synthetic"] is False


def test_create_duplicate_id_still_rejected(client):
    """Duplicate complaint_id must still return 400 even with scoring wired in."""
    make_complaint(client, "DUP-1")
    r = client.post(COLLECTION, json={"complaint_id": "DUP-1", "title": "Again"})
    assert r.status_code == 400


# ------------------------------------------------------------------ CSV import


def test_import_accepted_row_has_priority_score(client):
    """An accepted imported row must have a non-null priority_score in the DB."""
    csv_content = simple_csv(cid="IMP-1", severity="high")
    upload_csv(client, csv_content)
    body = get_complaint(client, "IMP-1")
    assert body["priority_score"] is not None
    assert isinstance(body["priority_score"], float)
    assert 0.0 <= body["priority_score"] <= 1.0


def test_import_accepted_row_has_priority_rationale(client):
    """An accepted imported row must have a parseable priority_rationale."""
    csv_content = simple_csv(cid="IMP-RAT-1", severity="medium")
    upload_csv(client, csv_content)
    body = get_complaint(client, "IMP-RAT-1")
    assert body["priority_rationale"] is not None
    rationale = json.loads(body["priority_rationale"])
    assert "rationale" in rationale
    assert "components" in rationale


def test_import_missing_severity_produces_uncertainty_flag(client):
    """Imported row with no severity_label must have missing_severity in uncertainty_flags."""
    csv_content = simple_csv(cid="IMP-MS-1", severity="")
    upload_csv(client, csv_content)
    body = get_complaint(client, "IMP-MS-1")
    rationale = json.loads(body["priority_rationale"])
    assert "missing_severity" in rationale["uncertainty_flags"]


def test_import_score_matches_production_scorer(client):
    """Persisted import score must equal the production scorer output for same inputs."""
    csv_content = simple_csv(cid="IMP-MATCH-1", severity="high",
                             subject="Bridge pothole", ctype="Roads")
    upload_csv(client, csv_content)
    body = get_complaint(client, "IMP-MATCH-1")
    assert body["priority_score"] is not None

    completeness_fields = ("description", "category", "severity_label", "location_label")
    present = sum(1 for f in completeness_fields if body.get(f) not in (None, ""))
    comp = round(present / len(completeness_fields), 4)

    inputs = PrioritizationInputs(
        complaint_id=body["complaint_id"],
        category=body["category"],
        severity_label=body["severity_label"],
        latitude=body["latitude"],
        longitude=body["longitude"],
        evidence_completeness=comp,
    )
    expected = RuleBasedPrioritizer().score(inputs).priority_score
    assert body["priority_score"] == expected


def test_import_dry_run_does_not_persist_score(client):
    """Dry-run import must not write anything to the database."""
    csv_content = simple_csv(cid="DRY-1")
    payload = csv_content.encode("utf-8")
    files = {"file": ("test.csv", io.BytesIO(payload), "text/csv")}
    data = {"source_label": "dry-test", "dry_run": "true"}
    r = client.post(CSV_URL, files=files, data=data)
    assert r.status_code == 200
    assert r.json()["dry_run"] is True
    r2 = client.get(f"{COLLECTION}/DRY-1")
    assert r2.status_code == 404


def test_import_duplicate_detection_still_works(client):
    """Idempotent re-import of same source+row must still skip the row."""
    csv_content = simple_csv(cid="IDEM-1")
    upload_csv(client, csv_content, source_label="idem-ds")
    result2 = upload_csv(client, csv_content, source_label="idem-ds")
    assert result2["skipped_rows"] == 1
    assert result2["accepted_rows"] == 0


def test_import_exact_duplicate_within_file_rejected(client):
    """Two different IDs with identical normalized content trigger exact_duplicate."""
    # exact_signature does NOT include complaint_id; two distinct IDs with
    # the same title/category/lat/lon/description/created_at are exact duplicates.
    header = "request_id,subject,complaint_type,lat,lon,severity_label,status\n"
    row_a = "IMP-EX-A,Pothole on Main,Roads,40.7,-74.0,,open\n"
    row_b = "IMP-EX-B,Pothole on Main,Roads,40.7,-74.0,,open\n"
    csv_content = header + row_a + row_b
    result = upload_csv(client, csv_content, source_label="ex-dup-ds")
    assert result["exact_duplicate_rows"] == 1
    assert result["accepted_rows"] == 1


def test_import_validation_rejection_not_scored(client):
    """A row rejected by validation must not appear in the database at all."""
    csv_content = "request_id,title\n,Valid title\n"
    upload_csv(client, csv_content, source_label="val-rej-ds")
    all_r = client.get(f"{BASE}/complaints")
    assert all_r.status_code == 200
    ids = [c["complaint_id"] for c in all_r.json()]
    assert "" not in ids
