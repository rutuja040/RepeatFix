"""Phase C: CSV import, column mapping, data quality, duplicates, provenance.

All CSV payloads here are small, inline, and clearly synthetic.
"""

import io
import json

from backend.core.config import settings

BASE = "/api/v1"
COMPLAINTS = f"{BASE}/complaints"
IMPORTS = f"{BASE}/imports"
CSV_URL = f"{IMPORTS}/csv"

HEADER = "request_id,subject,complaint_type,created_date,lat,lon,status\n"


def upload(
    client,
    content,
    *,
    mapping=None,
    source_label=None,
    synthetic=None,
    dry_run=None,
    filename="test.csv",
    content_type="text/csv",
):
    payload = content.encode("utf-8") if isinstance(content, str) else content
    files = {"file": (filename, io.BytesIO(payload), content_type)}
    data = {}
    if mapping is not None:
        data["mapping"] = json.dumps(mapping)
    if source_label is not None:
        data["source_label"] = source_label
    if synthetic is not None:
        data["synthetic"] = str(synthetic).lower()
    if dry_run is not None:
        data["dry_run"] = str(dry_run).lower()
    return client.post(CSV_URL, files=files, data=data)


def one_row(cid="R-1", subject="Pothole", ctype="Roads", date="2025-10-01T09:00:00Z",
            lat="40.7", lon="-74.0", status="open"):
    return HEADER + f"{cid},{subject},{ctype},{date},{lat},{lon},{status}\n"


def get_complaint(client, cid):
    r = client.get(f"{COMPLAINTS}/{cid}")
    assert r.status_code == 200, r.text
    return r.json()


def raw_payload(client, cid):
    return json.loads(get_complaint(client, cid)["raw_imported_values"])


# ---------------------------------------------------------------- file handling


def test_fields_endpoint(client):
    r = client.get(f"{IMPORTS}/fields")
    assert r.status_code == 200
    body = r.json()
    names = [f["name"] for f in body["fields"]]
    assert "complaint_id" in names and "title" in names
    assert set(body["required_fields"]) == {"complaint_id", "title"}
    assert body["limits"]["max_rows"] == settings.max_import_rows


def test_valid_csv_import(client):
    r = upload(client, one_row(), source_label="ds1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted_rows"] == 1
    assert body["status"] == "completed"
    assert body["import_id"]
    complaint = get_complaint(client, "R-1")
    assert complaint["title"] == "Pothole"
    assert complaint["created_at"] == "2025-10-01T09:00:00+00:00"


def test_empty_file(client):
    r = upload(client, b"")
    assert r.status_code == 200
    assert r.json()["status"] == "empty"
    assert r.json()["total_rows"] == 0


def test_whitespace_only_file(client):
    r = upload(client, "   \n  \n")
    assert r.status_code == 200
    assert r.json()["status"] == "empty"


def test_empty_dataset_header_only(client):
    r = upload(client, HEADER)
    assert r.status_code == 200
    assert r.json()["status"] == "empty"
    assert r.json()["total_rows"] == 0


def test_malformed_row_inconsistent_columns(client):
    content = HEADER + "R-1,Pothole,Roads,2025-10-01,40.7,-74.0,open,EXTRA\n"
    r = upload(client, content)
    assert r.status_code == 200
    body = r.json()
    assert body["accepted_rows"] == 0
    codes = [i["code"] for i in body["issues"]]
    assert "inconsistent_columns" in codes
    assert body["issues"][0]["status"] == "rejected"


def test_malformed_row_does_not_crash_import(client):
    content = HEADER + "R-1,Pothole,Roads,2025-10-01,40.7,-74.0,open\nR-2,T,Roads,2025-10-01,40.7\n"
    r = upload(client, content, source_label="mix")
    assert r.status_code == 200
    assert r.json()["accepted_rows"] == 1
    assert r.json()["rejected_rows"] == 1


def test_invalid_encoding_rejected(client):
    content = b"request_id,subject\n\xff\xfe\x00bad,value\n"
    r = upload(client, content)
    assert r.status_code == 400
    assert "utf-8" in r.json()["detail"]["message"].lower()


def test_missing_required_headers_rejected(client):
    r = upload(client, "foo,bar\n1,2\n")
    assert r.status_code == 422
    details = r.json()["detail"]["details"]
    assert set(details["missing_required"]) == {"complaint_id", "title"}


def test_missing_optional_columns_ok(client):
    content = "complaint_id,title\nR-1,Only the basics\n"
    r = upload(client, content, source_label="min")
    assert r.status_code == 200
    assert r.json()["accepted_rows"] == 1


def test_unsupported_file_type_rejected(client):
    r = upload(client, one_row(), filename="data.txt", content_type="text/plain")
    assert r.status_code == 400
    assert "file type" in r.json()["detail"]["message"].lower()


def test_file_size_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "max_import_size_mb", 0)
    r = upload(client, one_row())
    assert r.status_code == 413


def test_row_count_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "max_import_rows", 1)
    content = HEADER + "R-1,A,Roads,2025-10-01,40.7,-74.0,open\nR-2,B,Roads,2025-10-01,40.7,-74.0,open\n"
    r = upload(client, content)
    assert r.status_code == 422


# ---------------------------------------------------------------- column mapping


def test_automatic_alias_mapping(client):
    r = upload(client, one_row(), source_label="ds")
    mapping = r.json()["mapping"]
    assert mapping["complaint_id"] == "request_id"
    assert mapping["title"] == "subject"
    assert mapping["category"] == "complaint_type"
    assert mapping["latitude"] == "lat"
    assert mapping["longitude"] == "lon"


def test_explicit_mapping_override(client):
    content = "id,headline,kind\nR-9,My issue,Roads\n"
    r = upload(client, content, mapping={"complaint_id": "id", "title": "headline", "category": "kind"},
               source_label="exp")
    assert r.status_code == 200, r.text
    assert r.json()["mapping"]["title"] == "headline"
    assert r.json()["accepted_rows"] == 1


def test_ambiguous_columns_rejected(client):
    # Two plausible source columns for title -> must not choose silently.
    content = "complaint_id,title,subject\nR-1,A,B\n"
    r = upload(client, content)
    assert r.status_code == 422
    assert "title" in r.json()["detail"]["details"]["ambiguous"]


def test_missing_required_mapping_rejected(client):
    r = upload(client, "complaint_id,body\nR-1,text\n")
    assert r.status_code == 422
    assert "title" in r.json()["detail"]["details"]["missing_required"]


def test_unsupported_target_field_rejected(client):
    r = upload(client, one_row(), mapping={"priority_score": "subject"})
    assert r.status_code == 422


def test_unmatched_columns_reported_and_preserved(client):
    content = "complaint_id,title,internal_note\nR-1,Issue,keep me\n"
    r = upload(client, content, source_label="unm")
    assert r.status_code == 200
    assert "internal_note" in r.json()["unmatched_columns"]
    payload = raw_payload(client, "R-1")
    assert payload["source_values"]["internal_note"] == "keep me"


# ---------------------------------------------------------------- data quality


def test_invalid_date_row_reported(client):
    r = upload(client, one_row(date="not-a-date"))
    body = r.json()
    assert body["accepted_rows"] == 0
    issue = body["issues"][0]
    assert issue["code"] == "invalid_date"
    assert issue["field"] == "created_at"
    assert issue["row_number"] == 2
    assert issue["status"] == "rejected"


def test_valid_timestamp_normalized_to_utc(client):
    content = "complaint_id,title,created_at\nR-TZ,Timezones,2026-03-01T05:30:00+05:30\n"
    r = upload(client, content, source_label="tz")
    assert r.status_code == 200
    assert get_complaint(client, "R-TZ")["created_at"] == "2026-03-01T00:00:00+00:00"


def test_missing_optional_timestamp_flagged(client):
    content = "complaint_id,title\nR-ND,No date\n"
    r = upload(client, content, source_label="nd")
    body = r.json()
    assert body["accepted_rows"] == 1
    assert body["flagged_rows"] == 1
    assert any(i["code"] == "missing_created_at" for i in body["issues"])
    assert get_complaint(client, "R-ND")["created_at"] is not None


def test_invalid_latitude_rejected(client):
    r = upload(client, one_row(lat="91.0"))
    assert r.json()["issues"][0]["code"] == "out_of_range"


def test_invalid_longitude_rejected(client):
    r = upload(client, one_row(lon="-181.0"))
    assert r.json()["issues"][0]["code"] == "out_of_range"


def test_non_numeric_coordinate_rejected(client):
    r = upload(client, one_row(lat="north"))
    assert r.json()["issues"][0]["code"] == "invalid_number"


def test_incomplete_coordinates_flagged_not_rejected(client):
    r = upload(client, one_row(lat="40.7", lon=""))
    body = r.json()
    assert body["accepted_rows"] == 1
    assert any(i["code"] == "incomplete_coordinates" for i in body["issues"])


def test_missing_coordinates_accepted(client):
    content = "complaint_id,title\nR-NC,No coords\n"
    r = upload(client, content, source_label="nc")
    assert r.json()["accepted_rows"] == 1


def test_empty_required_text_rejected(client):
    content = "complaint_id,title\nR-1,   \n"
    r = upload(client, content, source_label="blank")
    assert r.json()["accepted_rows"] == 0
    assert r.json()["issues"][0]["code"] == "missing_required"


def test_excessively_long_value_rejected(client):
    long_title = "x" * 501
    content = f"complaint_id,title\nR-1,{long_title}\n"
    r = upload(client, content, source_label="long")
    assert r.json()["issues"][0]["code"] == "value_too_long"


def test_unknown_status_defaulted_with_warning(client):
    r = upload(client, one_row(status="banana"), source_label="st")
    body = r.json()
    assert body["accepted_rows"] == 1
    assert any(i["code"] == "unknown_status_defaulted" for i in body["issues"])
    assert get_complaint(client, "R-1")["status"] == "open"


def test_unknown_category_kept_verbatim(client):
    r = upload(client, one_row(ctype="Weird Category"), source_label="cat")
    assert r.status_code == 200
    assert get_complaint(client, "R-1")["category"] == "Weird Category"


# ---------------------------------------------------------------- duplicates


def test_duplicate_source_id_within_file_rejected(client):
    content = HEADER + "R-1,A,Roads,2025-10-01,40.7,-74.0,open\nR-1,B,Roads,2025-10-02,40.8,-74.1,open\n"
    r = upload(client, content, source_label="dup")
    body = r.json()
    assert body["accepted_rows"] == 1
    assert any(i["code"] == "duplicate_source_record_id" for i in body["issues"])


def test_exact_duplicate_records_rejected(client):
    # Different ids, identical normalized content.
    content = (
        HEADER
        + "A,Same title,Roads,2025-10-01,40.7,-74.0,open\n"
        + "B,Same title,Roads,2025-10-01,40.7,-74.0,open\n"
    )
    r = upload(client, content, source_label="exact")
    body = r.json()
    assert body["accepted_rows"] == 1
    assert body["exact_duplicate_rows"] == 1
    assert any(i["code"] == "exact_duplicate" for i in body["issues"])


def test_potential_duplicates_flagged_but_not_merged(client):
    content = (
        HEADER
        + "A,Pothole on Main Street,Roads,2025-10-01,40.700,-74.000,open\n"
        + "B,Pothole on Main St,Roads,2025-10-01,40.7001,-74.0001,open\n"
    )
    r = upload(client, content, source_label="pot")
    body = r.json()
    assert body["accepted_rows"] == 2  # never merged
    assert body["potential_duplicate_rows"] >= 1
    assert any(i["code"] == "potential_duplicate" and i["status"] == "flagged" for i in body["issues"])


def test_same_location_distinct_complaints_not_auto_merged(client):
    content = (
        HEADER
        + "A,Broken bench,Roads,2025-10-01,40.7,-74.0,open\n"
        + "B,Overflowing bin,Sanitation,2025-10-01,40.7,-74.0,open\n"
    )
    r = upload(client, content, source_label="loc")
    assert r.json()["accepted_rows"] == 2


# ---------------------------------------------------------------- idempotency


def test_repeated_import_is_idempotent(client):
    first = upload(client, one_row(), source_label="rep")
    assert first.json()["accepted_rows"] == 1
    second = upload(client, one_row(), source_label="rep")
    body = second.json()
    assert body["accepted_rows"] == 0
    assert body["skipped_rows"] == 1
    assert any(i["code"] == "skipped_existing" for i in body["issues"])
    r = client.get(COMPLAINTS)
    assert r.headers["X-Total-Count"] == "1"


def test_identity_derived_from_columns_when_no_label(client):
    first = upload(client, one_row())
    assert first.json()["accepted_rows"] == 1
    assert first.json()["dataset_identity"].startswith("cols:")
    second = upload(client, one_row())
    assert second.json()["skipped_rows"] == 1


def test_complaint_id_conflict_across_datasets_rejected(client):
    assert upload(client, one_row(), source_label="ds-a").json()["accepted_rows"] == 1
    r = upload(client, one_row(), source_label="ds-b")
    body = r.json()
    assert body["accepted_rows"] == 0
    assert any(i["code"] == "complaint_id_conflict" for i in body["issues"])


# ---------------------------------------------------------------- provenance & transactions


def test_import_metadata_created(client):
    r = upload(client, one_row(), source_label="meta")
    import_id = r.json()["import_id"]
    got = client.get(f"{IMPORTS}/{import_id}")
    assert got.status_code == 200
    body = got.json()
    assert body["import_id"] == import_id
    assert body["status"] == "completed"
    assert body["source_label"] == "meta"
    assert body["mapping"]["title"] == "subject"


def test_get_missing_import_returns_404(client):
    r = client.get(f"{IMPORTS}/does-not-exist")
    assert r.status_code == 404


def test_imported_record_links_to_source_identity(client):
    upload(client, one_row(), source_label="prov")
    payload = raw_payload(client, "R-1")
    assert payload["source_record_id"] == "R-1"
    assert payload["dataset_identity"] == "label:prov"
    assert payload["source_row_number"] == 2
    assert payload["source_values"]["request_id"] == "R-1"


def test_summary_counts_are_consistent(client):
    content = (
        HEADER
        + "A,ok,Roads,2025-10-01,40.7,-74.0,open\n"
        + "B,ok,Roads,2025-10-01,40.7,-74.0,open\n"  # exact dup of A
        + "C,,Roads,2025-10-01,40.7,-74.0,open\n"  # empty required
    )
    body = upload(client, content, source_label="counts").json()
    assert body["total_rows"] == body["accepted_rows"] + body["rejected_rows"] + body["skipped_rows"]


def test_import_history_listing(client):
    upload(client, one_row(cid="H-1"), source_label="hist")
    upload(client, one_row(cid="H-2"), source_label="hist2")
    r = client.get(IMPORTS)
    assert r.status_code == 200
    assert r.headers["X-Total-Count"] == "2"
    assert len(r.json()) == 2


def test_db_failure_marks_import_failed_and_rolls_back(client_no_raise, monkeypatch):
    from backend.services.imports import service

    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(service, "_insert_accepted_rows", boom)
    r = upload(client_no_raise, one_row(), source_label="boom")
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal server error"}
    assert "secret" not in r.text

    # No complaint was written...
    assert client_no_raise.get(COMPLAINTS).json() == []
    # ...and a failed import is recorded (never a false success).
    listed = client_no_raise.get(IMPORTS).json()
    assert len(listed) == 1
    assert listed[0]["status"] == "failed"
    assert listed[0]["accepted_rows"] == 0


def test_dry_run_does_not_persist(client):
    r = upload(client, one_row(), source_label="dry", dry_run=True)
    body = r.json()
    assert body["dry_run"] is True
    assert body["import_id"] is None
    assert client.get(COMPLAINTS).json() == []
    assert client.get(IMPORTS).json() == []


def test_synthetic_column_sets_flag(client):
    content = "complaint_id,title,synthetic\nR-S1,Synthetic thing,true\nR-R1,Real thing,false\n"
    r = upload(client, content, source_label="syn")
    assert r.status_code == 200
    assert get_complaint(client, "R-S1")["synthetic"] is True
    assert get_complaint(client, "R-R1")["synthetic"] is False


# ---------------------------------------------------------------- regressions


def test_existing_complaint_crud_still_works(client):
    created = client.post(
        COMPLAINTS, json={"complaint_id": "REG-1", "title": "Regression"}
    )
    assert created.status_code == 200
    patched = client.patch(f"{COMPLAINTS}/REG-1", json={"status": "resolved"})
    assert patched.status_code == 200
    assert patched.json()["resolved_at"] is not None
    hist = client.get(f"{COMPLAINTS}/REG-1/history").json()
    assert any(h["field_name"] == "status" for h in hist)
