from __future__ import annotations

from fastapi.testclient import TestClient

from app.worker_server import create_app


def test_health_endpoint_returns_ok(tmp_path, monkeypatch) -> None:
    # Avoid startup network validation in unit tests.
    monkeypatch.setattr("app.worker_server.validate_all", lambda *, settings: None)
    app = create_app(work_root=str(tmp_path))
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_event_requires_repo_and_issue_on_first_start(tmp_path, monkeypatch) -> None:
    # Avoid startup network validation in unit tests.
    monkeypatch.setattr("app.worker_server.validate_all", lambda *, settings: None)
    app = create_app(work_root=str(tmp_path))
    with TestClient(app) as client:
        resp = client.post("/event", json={"type": "start", "payload": {}})
        assert resp.status_code == 400
