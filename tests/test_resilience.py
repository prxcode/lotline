import json
from zetaris import client
from world_model.store import WorldStoreService, event_log_path


def test_zetaris_down_falls_back_to_local(monkeypatch):
    monkeypatch.setenv("DATA_BACKEND", "zetaris")
    monkeypatch.setenv("ZETARIS_REST_URL", "http://localhost:9")
    monkeypatch.setenv("ZETARIS_API_KEY", "x")
    monkeypatch.setenv("ZETARIS_TABLES", "inventory=lotline.inventory")
    try:
        assert client.check_backend()
        assert client.backend_name() == "local"
        assert "stock" in client.default_source_map()
    finally:
        monkeypatch.setenv("DATA_BACKEND", "local")
        client.check_backend()


def test_world_model_writes_an_event_log(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    store = WorldStoreService(":memory:")
    store.upsert("candidate", "c1", {"run_id": "run_t", "_revisions": 0, "_provenance": [{"agent": "Matcher", "source": "inventory.csv line 2"}]})
    store.relate("run_t", "c1", "may_be", "r1", "Matcher")
    events = [json.loads(x) for x in event_log_path("run_t").read_text(encoding="utf-8").splitlines()]
    assert [e["event"] for e in events] == ["entity.upserted", "relation.added"]
    assert events[0]["agent"] == "Matcher" and events[0]["source"] == "inventory.csv line 2"
    assert store.neighbors("run_t", "c1")[0]["dst"] == "r1"


def test_pull_is_held_if_the_source_record_changed(monkeypatch):
    from agents import critic
    candidates = [{"item_id": "X", "brand": "B", "product": "P", "upc": "1", "lot_code": "L1", "best_by": ""}]
    verdicts = [{"item_id": "X", "verdict": "CONFIRMED_MATCH", "action": "pull_and_notify", "rationale": "matched", "rule_checks": []}]
    monkeypatch.setattr(critic, "get_stock_items", lambda sources, ids: {"X": {**candidates[0], "lot_code": "L2"}})
    out = critic._verify_pulls({"run_id": "t", "source_map": {}}, candidates, verdicts)
    assert out[0]["verdict"] == "HUMAN_CHECK" and "lot_code" in out[0]["rationale"]
