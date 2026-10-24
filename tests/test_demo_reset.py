import os

from fastapi.testclient import TestClient

from api.main import app
from world_model import store

client = TestClient(app)


def test_demo_reset_is_404_unless_demo_mode_is_on(monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    assert client.get("/api/config").json()["demo_mode"] is False
    assert client.post("/api/demo/reset").status_code == 404


def test_demo_reset_clears_pulled_items_and_aliases(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    assert client.get("/api/config").json()["demo_mode"] is True

    store().put_case("R-001", {"recall_id": "R-001", "pulled": {"INV-0121": {"by": "demo"}}, "notices": {}})
    store().approve_alias("Mezzetta", "Mezzeta")
    assert store().get_case("R-001")["pulled"]
    assert store().learned_aliases("Mezzetta")

    res = client.post("/api/demo/reset")
    assert res.status_code == 200
    assert res.json() == {"reset": True}
    assert store().get_case("R-001") is None
    assert store().learned_aliases("Mezzetta") == []
