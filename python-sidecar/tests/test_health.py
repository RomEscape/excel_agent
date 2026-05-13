"""Tests for the health endpoint."""

from fastapi.testclient import TestClient

from office_claw_sidecar.main import app

client = TestClient(app)


def test_health_returns_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "ollama_status" in data
