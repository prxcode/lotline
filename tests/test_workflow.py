"""The coordinator's workflow: the inbox screen, and closing a hand check with a reading from the package."""
import pytest

from graph.lotline_graph import run_recall
from agents.resolution import record_reading
from agents.screen import screen_all


@pytest.fixture
def r001(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "data")
    return run_recall("R-001", sample_mode=True)["output"]


def test_screen_agrees_with_the_full_trace(r001, monkeypatch):
    monkeypatch.setenv("DATA_DIR", "data")
    row = next(r for r in screen_all(refresh=True) if r["recall_id"] == "R-001")
    assert (row["pull"], row["check"]) == (len(r001["pull_list"]), len(r001["human_checks"]))
    assert row["status"] == "affected" and row["units_on_hand"] == r001["total_units_to_pull"]


def test_reading_in_range_moves_the_item_to_pull(r001):
    item = next(v for v in r001["human_checks"] if v["item_id"] == "INV-0129")
    before = len(r001["pull_list"])
    out = record_reading(r001, "INV-0129", {"lot_code": "0188 262 21:00"}, "Asha")
    assert (out["from"], out["to"], out["list"]) == ("HUMAN_CHECK", "CONFIRMED_MATCH", "pull_list")
    assert len(r001["pull_list"]) == before + 1 and not any(v["item_id"] == "INV-0129" for v in r001["human_checks"])
    moved = next(v for v in r001["pull_list"] if v["item_id"] == "INV-0129")
    assert moved["reading"]["by"] == "Asha" and moved["source"] == "rules on a person's reading"
    assert any(c["rule"] == "person_reading" for c in moved["rule_checks"])
    # Its shipments now ask the agency to remove, not hold
    sent = [i for n in r001["agency_notices"] for i in n["items"] if i["item_id"] == "INV-0129"]
    assert sent and all(i["item_verdict"] == "CONFIRMED_MATCH" for i in sent)
    assert item["verdict"] == "HUMAN_CHECK"


def test_reading_that_conflicts_stays_with_a_person(r001):
    # A lot outside the notice, but a best-by date on it: the evidence disagrees, so a person decides
    out = record_reading(r001, "INV-0129", {"lot_code": "0188 268 11:30"}, "Asha")
    assert out["to"] == "HUMAN_CHECK" and "disagree" in out["item"]["rationale"]


def test_reading_that_rules_it_out_drops_it_from_agency_notices(r001):
    out = record_reading(r001, "INV-0129", {"lot_code": "0188 268 11:30", "best_by": "2026-10-24"}, "Asha")
    assert out["to"] == "NO_MATCH" and out["list"] == "near_misses"
    assert not any(i["item_id"] == "INV-0129" for n in r001["agency_notices"] for i in n["items"])


def test_empty_reading_is_refused(r001):
    with pytest.raises(ValueError):
        record_reading(r001, "INV-0129", {"lot_code": "  "}, "Asha")


def test_real_recalls_not_in_our_stock_are_clear_and_say_where_we_looked(monkeypatch):
    # R-011 to R-014: real FDA notices from October 2026 for products the food bank does not hold
    monkeypatch.setenv("DATA_DIR", "data")
    screen = {r["recall_id"]: r for r in screen_all(refresh=True)}
    for rid in ("R-011", "R-012", "R-013", "R-014"):
        assert screen[rid]["status"] == "clear", rid
        pack = run_recall(rid, sample_mode=True)["output"]
        assert not pack["pull_list"] and not pack["human_checks"] and not pack["agency_notices"]
        assert pack["verdict_summary"].startswith("Not affected. Searched inventory (180 records)")
