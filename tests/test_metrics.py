def test_metrics_endpoint_returns_prometheus_text(client):
    resp = client.get("/metrics")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "rarebirdalert_sightings_total" in resp.text
    assert "rarebirdalert_notifications_sent_total" in resp.text
    assert "rarebirdalert_poll_duration_seconds" in resp.text


def test_metrics_endpoint_open_when_no_token_configured(client, monkeypatch):
    monkeypatch.setattr("app.main.METRICS_TOKEN", None)
    resp = client.get("/metrics")
    assert resp.status_code == 200


def test_metrics_endpoint_rejects_missing_token_when_configured(client, monkeypatch):
    monkeypatch.setattr("app.main.METRICS_TOKEN", "secret123")
    resp = client.get("/metrics")
    assert resp.status_code == 401


def test_metrics_endpoint_rejects_wrong_token(client, monkeypatch):
    monkeypatch.setattr("app.main.METRICS_TOKEN", "secret123")
    resp = client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_metrics_endpoint_accepts_the_correct_token(client, monkeypatch):
    monkeypatch.setattr("app.main.METRICS_TOKEN", "secret123")
    resp = client.get("/metrics", headers={"Authorization": "Bearer secret123"})
    assert resp.status_code == 200
