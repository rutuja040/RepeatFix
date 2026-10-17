"""Phase B: complaint management, history, filtering, pagination, validation."""

BASE = "/api/v1"
COLLECTION = f"{BASE}/complaints"


def make(client, cid, title="Test complaint", **extra):
    payload = {"complaint_id": cid, "title": title}
    payload.update(extra)
    return client.post(COLLECTION, json=payload)


def create_ok(client, cid, **extra):
    r = make(client, cid, **extra)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- creation


def test_create_valid_complaint_roundtrip(client):
    body = create_ok(
        client,
        "C-1",
        title="Pothole on 5th Ave",
        description="Large pothole",
        category="Roads",
        latitude=40.7128,
        longitude=-74.0060,
    )
    assert body["complaint_id"] == "C-1"
    assert body["title"] == "Pothole on 5th Ave"
    assert body["category"] == "Roads"
    assert body["latitude"] == 40.7128
    assert body["longitude"] == -74.0060
    assert body["status"] == "open"
    assert body["synthetic"] is False
    assert body["created_at"] is not None
    assert body["resolved_at"] is None


def test_create_timestamps_are_utc(client):
    body = create_ok(client, "C-TZ")
    assert body["created_at"].endswith("+00:00")
    assert body["last_updated_at"].endswith("+00:00")


def test_create_normalizes_offset_to_utc(client):
    body = create_ok(client, "C-OFF", created_at="2026-03-01T05:30:00+05:30")
    assert body["created_at"] == "2026-03-01T00:00:00+00:00"


def test_create_blank_title_rejected(client):
    r = client.post(COLLECTION, json={"complaint_id": "X", "title": "   "})
    assert r.status_code == 422


def test_create_missing_title_rejected(client):
    r = client.post(COLLECTION, json={"complaint_id": "X"})
    assert r.status_code == 422


def test_create_blank_complaint_id_rejected(client):
    r = client.post(COLLECTION, json={"complaint_id": "   ", "title": "t"})
    assert r.status_code == 422


def test_create_invalid_latitude_rejected(client):
    r = make(client, "C-LAT", latitude=91.0)
    assert r.status_code == 422


def test_create_invalid_longitude_rejected(client):
    r = make(client, "C-LON", longitude=-181.0)
    assert r.status_code == 422


def test_create_invalid_status_rejected(client):
    r = make(client, "C-STAT", status="banana")
    assert r.status_code == 422


def test_create_unknown_field_rejected(client):
    r = client.post(
        COLLECTION,
        json={"complaint_id": "C-X", "title": "t", "priority_score": 0.9},
    )
    assert r.status_code == 422


def test_duplicate_complaint_id_rejected(client):
    assert make(client, "C-DUP").status_code == 200
    r = make(client, "C-DUP")
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"].lower()


# ---------------------------------------------------------------- retrieval


def test_get_complaint_by_id(client):
    create_ok(client, "C-GET", title="See me")
    r = client.get(f"{COLLECTION}/C-GET")
    assert r.status_code == 200
    assert r.json()["title"] == "See me"


def test_get_missing_complaint_returns_404(client):
    r = client.get(f"{COLLECTION}/NOPE")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


def test_missing_optional_fields_returned_as_null(client):
    body = create_ok(client, "C-NULL")
    for field in ("description", "category", "latitude", "longitude", "severity_label",
                  "assigned_department", "resolved_at"):
        assert body[field] is None


# ---------------------------------------------------------------- updates


def test_update_permitted_fields(client):
    create_ok(client, "C-UPD", title="old")
    r = client.patch(
        f"{COLLECTION}/C-UPD",
        json={"title": "new", "category": "Roads", "assigned_department": "DPW"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "new"
    assert body["category"] == "Roads"
    assert body["assigned_department"] == "DPW"


def test_update_missing_complaint_returns_404(client):
    r = client.patch(f"{COLLECTION}/NOPE", json={"title": "x"})
    assert r.status_code == 404


def test_update_cannot_change_created_at(client):
    create_ok(client, "C-CA", created_at="2026-01-01T00:00:00+00:00")
    r = client.patch(f"{COLLECTION}/C-CA", json={"created_at": "2020-01-01T00:00:00+00:00"})
    assert r.status_code == 422


def test_update_cannot_change_complaint_id(client):
    create_ok(client, "C-CID")
    r = client.patch(f"{COLLECTION}/C-CID", json={"complaint_id": "OTHER"})
    assert r.status_code == 422


def test_update_cannot_set_priority_fields(client):
    create_ok(client, "C-PRIO")
    r = client.patch(f"{COLLECTION}/C-PRIO", json={"priority_score": 1.0})
    assert r.status_code == 422


def test_update_created_at_preserved(client):
    original = create_ok(client, "C-KEEP", created_at="2026-01-01T00:00:00+00:00")
    r = client.patch(f"{COLLECTION}/C-KEEP", json={"title": "changed"})
    assert r.status_code == 200
    assert r.json()["created_at"] == original["created_at"]


def test_update_invalid_latitude_rejected(client):
    create_ok(client, "C-ULAT")
    r = client.patch(f"{COLLECTION}/C-ULAT", json={"latitude": 200.0})
    assert r.status_code == 422


# ---------------------------------------------------------------- history


def history(client, cid):
    r = client.get(f"{COLLECTION}/{cid}/history")
    assert r.status_code == 200, r.text
    return r.json()


def test_creation_records_history_entry(client):
    create_ok(client, "C-H1")
    entries = history(client, "C-H1")
    assert len(entries) == 1
    assert entries[0]["field_name"] == "status"
    assert entries[0]["old_value"] is None
    assert entries[0]["new_value"] == "open"
    assert entries[0]["complaint_id"] == "C-H1"


def test_update_records_field_history(client):
    create_ok(client, "C-H2", title="before")
    client.patch(f"{COLLECTION}/C-H2", json={"title": "after", "category": "Roads"})
    entries = history(client, "C-H2")
    by_field = {e["field_name"]: e for e in entries}
    assert by_field["title"]["old_value"] == "before"
    assert by_field["title"]["new_value"] == "after"
    assert by_field["category"]["old_value"] is None
    assert by_field["category"]["new_value"] == "Roads"


def test_update_same_value_records_no_history(client):
    create_ok(client, "C-H3", title="same")
    before = len(history(client, "C-H3"))
    client.patch(f"{COLLECTION}/C-H3", json={"title": "same"})
    assert len(history(client, "C-H3")) == before


def test_history_is_append_only(client):
    create_ok(client, "C-H4", title="v1")
    client.patch(f"{COLLECTION}/C-H4", json={"title": "v2"})
    client.patch(f"{COLLECTION}/C-H4", json={"title": "v3"})
    titles = [e for e in history(client, "C-H4") if e["field_name"] == "title"]
    assert [t["new_value"] for t in titles] == ["v2", "v3"]
    assert [t["old_value"] for t in titles] == ["v1", "v2"]


def test_history_missing_complaint_returns_404(client):
    r = client.get(f"{COLLECTION}/NOPE/history")
    assert r.status_code == 404


def test_status_transition_records_history_and_resolved_at(client):
    create_ok(client, "C-ST1")
    r = client.patch(f"{COLLECTION}/C-ST1", json={"status": "resolved"})
    assert r.status_code == 200
    assert r.json()["status"] == "resolved"
    assert r.json()["resolved_at"] is not None
    fields = [e["field_name"] for e in history(client, "C-ST1")]
    assert "status" in fields
    assert "resolved_at" in fields


def test_reopen_clears_resolved_at(client):
    create_ok(client, "C-ST2")
    client.patch(f"{COLLECTION}/C-ST2", json={"status": "resolved"})
    r = client.patch(f"{COLLECTION}/C-ST2", json={"status": "open"})
    assert r.status_code == 200
    assert r.json()["resolved_at"] is None


def test_resolved_to_closed_keeps_resolved_at(client):
    create_ok(client, "C-ST3")
    resolved = client.patch(f"{COLLECTION}/C-ST3", json={"status": "resolved"}).json()
    closed = client.patch(f"{COLLECTION}/C-ST3", json={"status": "closed"}).json()
    assert closed["resolved_at"] == resolved["resolved_at"]


def test_status_same_value_no_history_noise(client):
    create_ok(client, "C-ST4")
    before = len(history(client, "C-ST4"))
    client.patch(f"{COLLECTION}/C-ST4", json={"status": "open"})
    assert len(history(client, "C-ST4")) == before


# ---------------------------------------------------------------- filtering


def seed_filters(client):
    create_ok(client, "F-1", title="Alpha pothole", category="Roads",
               status="open", location_label="Downtown", created_at="2026-01-01T00:00:00+00:00")
    create_ok(client, "F-2", title="Beta garbage", category="Sanitation",
               status="resolved", location_label="Uptown", created_at="2026-02-01T00:00:00+00:00")
    create_ok(client, "F-3", title="Gamma pothole", category="roads",
               status="in_progress", location_label="Downtown", created_at="2026-03-01T00:00:00+00:00")


def test_filter_by_status(client):
    seed_filters(client)
    r = client.get(COLLECTION, params={"status": "open"})
    assert r.status_code == 200
    ids = [c["complaint_id"] for c in r.json()]
    assert ids == ["F-1"]


def test_filter_by_category_case_insensitive(client):
    seed_filters(client)
    r = client.get(COLLECTION, params={"category": "ROADS"})
    ids = sorted(c["complaint_id"] for c in r.json())
    assert ids == ["F-1", "F-3"]


def test_filter_by_location(client):
    seed_filters(client)
    r = client.get(COLLECTION, params={"location": "downtown"})
    ids = sorted(c["complaint_id"] for c in r.json())
    assert ids == ["F-1", "F-3"]


def test_text_search(client):
    seed_filters(client)
    r = client.get(COLLECTION, params={"q": "garbage"})
    ids = [c["complaint_id"] for c in r.json()]
    assert ids == ["F-2"]


def test_text_search_escapes_wildcards(client):
    seed_filters(client)
    r = client.get(COLLECTION, params={"q": "%"})
    assert r.status_code == 200
    assert r.json() == []


def test_filter_by_resolved(client):
    seed_filters(client)
    r = client.get(COLLECTION, params={"resolved": True})
    ids = [c["complaint_id"] for c in r.json()]
    assert ids == ["F-2"]


def test_filter_by_synthetic(client):
    create_ok(client, "S-1", synthetic=True)
    create_ok(client, "S-2", synthetic=False)
    r = client.get(COLLECTION, params={"synthetic": True})
    assert [c["complaint_id"] for c in r.json()] == ["S-1"]


def test_date_range_filter(client):
    seed_filters(client)
    r = client.get(COLLECTION, params={
        "created_from": "2026-01-15T00:00:00+00:00",
        "created_to": "2026-02-15T00:00:00+00:00",
    })
    assert [c["complaint_id"] for c in r.json()] == ["F-2"]


def test_invalid_status_filter_rejected(client):
    r = client.get(COLLECTION, params={"status": "nope"})
    assert r.status_code == 422


def test_created_from_after_created_to_rejected(client):
    r = client.get(COLLECTION, params={
        "created_from": "2026-02-01T00:00:00+00:00",
        "created_to": "2026-01-01T00:00:00+00:00",
    })
    assert r.status_code == 422


# ---------------------------------------------------------------- pagination / sorting


def test_pagination_and_total_header(client):
    for i in range(5):
        create_ok(client, f"P-{i}", created_at=f"2026-01-0{i + 1}T00:00:00+00:00")
    r = client.get(COLLECTION, params={"skip": 1, "limit": 2, "sort_by": "created_at", "sort_dir": "asc"})
    assert r.status_code == 200
    assert r.headers["X-Total-Count"] == "5"
    assert [c["complaint_id"] for c in r.json()] == ["P-1", "P-2"]


def test_pagination_total_reflects_filter(client):
    for i in range(5):
        create_ok(client, f"PF-{i}", category="A" if i < 2 else "B")
    r = client.get(COLLECTION, params={"category": "A", "limit": 1})
    assert r.headers["X-Total-Count"] == "2"
    assert len(r.json()) == 1


def test_limit_validation(client):
    assert client.get(COLLECTION, params={"limit": 0}).status_code == 422
    assert client.get(COLLECTION, params={"limit": 201}).status_code == 422
    assert client.get(COLLECTION, params={"skip": -1}).status_code == 422


def test_sort_created_at_desc_and_asc(client):
    create_ok(client, "SR-1", created_at="2026-01-01T00:00:00+00:00")
    create_ok(client, "SR-2", created_at="2026-02-01T00:00:00+00:00")
    asc = client.get(COLLECTION, params={"sort_by": "created_at", "sort_dir": "asc"}).json()
    desc = client.get(COLLECTION, params={"sort_by": "created_at", "sort_dir": "desc"}).json()
    assert [c["complaint_id"] for c in asc] == ["SR-1", "SR-2"]
    assert [c["complaint_id"] for c in desc] == ["SR-2", "SR-1"]


def test_sort_by_title(client):
    create_ok(client, "ST-1", title="Zebra")
    create_ok(client, "ST-2", title="Apple")
    r = client.get(COLLECTION, params={"sort_by": "title", "sort_dir": "asc"})
    assert [c["complaint_id"] for c in r.json()] == ["ST-2", "ST-1"]


def test_invalid_sort_by_rejected(client):
    assert client.get(COLLECTION, params={"sort_by": "password"}).status_code == 422


def test_invalid_sort_dir_rejected(client):
    assert client.get(COLLECTION, params={"sort_dir": "sideways"}).status_code == 422


# ---------------------------------------------------------------- meta


def test_meta_endpoint(client):
    seed_filters(client)
    r = client.get(f"{BASE}/meta/complaints")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert "open" in body["statuses"]
    cats = {c["category"]: c["count"] for c in body["categories"]}
    assert cats["Roads"] == 1  # case-sensitive storage; two rows used "Roads"/"roads"


# ---------------------------------------------------------------- error safety


def test_unhandled_error_returns_generic_500(client_no_raise, monkeypatch):
    from backend.services.complaints import service

    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(service, "get_by_complaint_id", boom)
    r = client_no_raise.get(f"{COLLECTION}/ANY")
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal server error"}
    assert "secret" not in r.text
