from fastapi.testclient import TestClient
from monitor.main import create_app
from monitor.settings import AppSettings


def test_health_reports_ok(tmp_path):
    app = create_app(AppSettings(_env_file=None, db_path=tmp_path / "app.db"))

    body = TestClient(app).get("/health").json()

    assert body["status"] == "ok"
    assert body["mqtt_connected"] is False
    app.state.runtime.store.close()
