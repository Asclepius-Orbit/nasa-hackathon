from fastapi.testclient import TestClient
from monitor.main import app


def test_health_reports_ok():
    assert TestClient(app).get("/health").json() == {"status": "ok"}
