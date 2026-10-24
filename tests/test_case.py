"""A recall as a case: status follows from the work, and a case closes only when nothing is left."""
import pytest

from agents import case as cases
from agents.record import render
from agents.resolution import record_reading
from graph.lotline_graph import run_recall
from world_model import store


@pytest.fixture
def r001(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", "data")
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    with store()._lock:
        store()._conn.execute("DELETE FROM cases WHERE recall_id='R-001'")
    pack = run_recall("R-001", sample_mode=True)["output"]
    return pack, cases.load("R-001", pack["run_id"])


def test_case_moves_through_its_steps_and_closes_only_when_done(r001):
    pack, case = r001
    assert cases.status(case, pack)["state"] == "pulling"
    with pytest.raises(ValueError):
        cases.close(case, pack, "Asha")
    for v in pack["pull_list"]:
        cases.mark_pulled(case, pack, v["item_id"], True, "Asha")
    assert cases.status(case, pack)["state"] == "checking"

    # A reading that puts another item on the pull list sends the case back to pulling
    record_reading(pack, "INV-0129", {"lot_code": "0188 262 21:00"}, "Asha")
    assert cases.status(case, pack)["state"] == "pulling"
    cases.mark_pulled(case, pack, "INV-0129", True, "Asha")
    assert cases.status(case, pack)["state"] == "notifying"

    agencies = [n["agency_id"] for n in pack["agency_notices"]]
    with pytest.raises(ValueError):
        cases.mark_notice(case, pack, agencies[0], "replied", "Asha", 0)
    for a in agencies:
        cases.mark_notice(case, pack, a, "sent", "Asha")
    assert cases.status(case, pack)["state"] == "waiting"
    for a in agencies:
        cases.mark_notice(case, pack, a, "replied", "Asha", 0)
    assert cases.status(case, pack)["state"] == "ready"
    out = cases.close(case, pack, "Asha")
    assert out["status"]["state"] == "closed" and store().get_case("R-001")["closed_by"] == "Asha"


def test_unknown_items_and_agencies_are_refused(r001):
    pack, case = r001
    with pytest.raises(LookupError):
        cases.mark_pulled(case, pack, "INV-9999", True, "Asha")
    with pytest.raises(LookupError):
        cases.mark_notice(case, pack, "AG-99", "sent", "Asha")


def test_record_shows_only_what_was_read_and_escapes_text(r001):
    pack, case = r001
    record_reading(pack, "INV-0129", {"lot_code": "0188 262 21:00"}, "<b>Asha</b>")
    html = render(pack, case, cases.status(case, pack), [])
    assert "lot code 0188 262 21:00" in html and "best by" not in html.split("Package readings")[1].split("</table>")[0]
    assert "<b>Asha</b>" not in html and "&lt;b&gt;Asha&lt;/b&gt;" in html
