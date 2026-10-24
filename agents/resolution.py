"""Package readings: record what is printed and run the same rules again."""
from datetime import datetime, timezone

from agents.base import log_step
from agents.rules import evaluate
from agents.reporter import _draft_notice, _RELATED
from world_model import ingest, entity_id

READ_FIELDS = ("lot_code", "best_by", "upc", "brand", "product")
_LISTS = ("pull_list", "human_checks", "near_misses")
_DESTINATION = {"pull_and_notify": "pull_list", "human_verify": "human_checks", "monitor": "near_misses", "clear": "near_misses"}


def _units(items) -> float:
    return sum(float(v.get("quantity") or 0) for v in items)


def _refresh(pack: dict):
    """Recompute everything that depends on the verdicts: notices, shipment flags, impact numbers."""
    verdicts = {v["item_id"]: v for name in _LISTS for v in pack[name]}
    for d in pack["distributed_items"]:
        d["item_verdict"] = verdicts.get(d["item_id"], {}).get("verdict", d.get("item_verdict"))
    notices = []
    for n in pack["agency_notices"]:
        # An item cleared by a reading no longer needs the agency's attention
        items = [{**i, "item_verdict": verdicts.get(i["item_id"], {}).get("verdict", i.get("item_verdict"))} for i in n["items"]]
        items = [i for i in items if i["item_verdict"] not in ("NO_MATCH", "NEAR_MISS")]
        if items:
            notices.append({**n, "items": items, "draft_message": _draft_notice({**n, "items": items}, pack["recall"])})
    pack["agency_notices"] = notices
    related = [v for v in verdicts.values() if v.get("brand_match", {}).get("method") in _RELATED]
    pack["impact"].update(units_to_pull=_units(pack["pull_list"]), units_to_check=_units(pack["human_checks"]),
                          units_kept_in_use=_units(v for v in related if v["action"] in ("monitor", "clear")),
                          agencies_to_notify=len(notices))
    pack["total_units_to_pull"] = pack["impact"]["units_to_pull"]


def record_reading(pack: dict, item_id: str, reading: dict, read_by: str, trace_path: str | None = None) -> dict:
    current = next(((name, v) for name in _LISTS for v in pack[name] if v["item_id"] == item_id), None)
    if not current:
        raise LookupError(f"{item_id} is not part of this trace")
    old_list, item = current
    clean = {k: str(reading[k]).strip() for k in READ_FIELDS if str(reading.get(k) or "").strip()}
    if not clean:
        raise ValueError("record at least one of: lot or product code, best-by date, UPC")
    who = (read_by or "").strip()[:60] or "a volunteer"
    run_id = pack["run_id"]

    log_step(run_id, "Person", "reading", {}, trace_path, action="record_package_reading",
             input_summary=f"{item_id}: {item.get('rationale', '')[:120]}",
             output_summary=f"{who} read " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in clean.items()),
             target_agent="Critic")

    decided = evaluate(pack["match_criteria"], {**item, **clean})
    read_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    decided["rule_checks"].append({"rule": "person_reading", "passed": True,
                                   "note": f"Read on the package by {who}: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in clean.items())})
    updated = {**item, **clean, **decided, "source": "rules on a person's reading",
               "reading": {**clean, "by": who, "at": read_at}, "previous_verdict": item["verdict"]}

    pack[old_list] = [v for v in pack[old_list] if v["item_id"] != item_id]
    pack[_DESTINATION[updated["action"]]].append(updated)
    _refresh(pack)
    pack.setdefault("updates", []).append({"item_id": item_id, "from": item["verdict"], "to": updated["verdict"],
                                           "by": who, "at": read_at, "reading": clean})

    wm = ingest()
    wm.ingest("critique", {**updated, "round": "reading", "source_ref": f"package read by {who}"}, "item_id", run_id, agent="Person")
    wm.relate(run_id, entity_id("critique", run_id, item_id), "judges", entity_id("candidate", run_id, f"cand_{item_id}"), "Person")

    moved = updated["verdict"] != item["verdict"]
    log_step(run_id, "Critic", "rejudge", {}, trace_path, action="rejudge_with_reading",
             input_summary=f"{item_id} with the reading from {who}",
             output_summary=f"{item_id}: {item['verdict']} -> {updated['verdict']}. {updated['rationale'][:160]}" if moved
                            else f"{item_id} still needs a person: {updated['rationale'][:160]}",
             target_agent="Reporter", status="success" if moved else "needs_revision")
    return {"item": updated, "from": item["verdict"], "to": updated["verdict"], "list": _DESTINATION[updated["action"]]}
