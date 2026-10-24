"""Quick rules-only screen of every notice for the recall list."""
import time

from agents.criteria import parse_line
from agents.rules import evaluate
from agents.matcher import _search_terms
from zetaris.client import default_source_map, list_recalls, get_distinct_brands, find_stock, shipped_totals, get_shipments
from world_model import store

_cache: dict = {"at": 0.0, "result": None}
CACHE_SECONDS = 60


def screen_recall(recall: dict, sources: dict) -> dict:
    lines = [parse_line(r) for r in recall.get("lines", [])]
    brands = list(dict.fromkeys(ln["brand"] for ln in lines if ln["brand"]))
    criteria = {"lines": lines, "brands": brands, "aliases": {},
                "learned": {b: [a["alias"] for a in store().learned_aliases(b)] for b in brands}}
    found, upc_keys, words = _search_terms(criteria, get_distinct_brands(sources))
    rows = find_stock(sources, list(found), upc_keys, words)
    verdicts = [(r, evaluate(criteria, r)) for r in rows]
    pull = [r for r, v in verdicts if v["action"] == "pull_and_notify"]
    check = [r for r, v in verdicts if v["action"] == "human_verify"]
    flagged = [r["item_id"] for r in pull + check]
    if "quantity_received" in sources["stock"]["mapping"]:
        shipped = shipped_totals(sources, flagged)
        on_hand = sum(max(0.0, float(r.get("quantity_received") or 0) - shipped.get(r["item_id"], 0.0)) for r in pull)
    else:
        on_hand = sum(float(r.get("quantity_on_hand") or 0) for r in pull)
    agencies = {s["agency_id"] for s in get_shipments(sources, flagged)}
    status = "affected" if pull else "check" if check else "clear"
    return {"recall_id": recall["recall_id"], "status": status, "pull": len(pull), "check": len(check),
            "units_on_hand": on_hand, "agencies": len(agencies)}


def screen_all(refresh: bool = False) -> list[dict]:
    if not refresh and _cache["result"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["result"]
    sources = default_source_map()
    result = [screen_recall(r, sources) for r in list_recalls(sources)]
    _cache.update(at=time.time(), result=result)
    return result


def invalidate():
    _cache.update(at=0.0, result=None)
