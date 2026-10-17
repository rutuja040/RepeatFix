def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_root(client):
    r = client.get("/")
    assert r.status_code == 200


def test_create_and_get_complaint(client):
    payload = {
        "complaint_id": "HEALTH-1",
        "title": "Streetlight out",
        "synthetic": True,
        "category": "Streetlights",
    }
    r = client.post("/api/v1/complaints", json=payload)
    assert r.status_code == 200, r.text
    r2 = client.get("/api/v1/complaints")
    assert r2.status_code == 200
    ids = [c["complaint_id"] for c in r2.json()]
    assert "HEALTH-1" in ids


def test_duplicate_complaint_id_rejected(client):
    payload = {"complaint_id": "HEALTH-DUP", "title": "a", "synthetic": True}
    r1 = client.post("/api/v1/complaints", json=payload)
    assert r1.status_code == 200
    r2 = client.post("/api/v1/complaints", json=payload)
    assert r2.status_code == 400


def test_missing_required_field(client):
    r = client.post("/api/v1/complaints", json={"description": "no title"})
    assert r.status_code == 422
