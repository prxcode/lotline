"""Recall cases. The status is computed from the work, never set by hand."""
import os
from datetime import datetime, timezone

from agents.base import log_step
from world_model import store, ingest

STATUS_TEXT = {
    "pulling": "Items still to pull",
    "checking": "Packages still to read",
    "notifying": "Agency notices still to send",
    "waiting": "Waiting for agency replies",
    "ready": "Ready to close",
    "closed": "Closed",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(recall_id: str, run_id: str) -> dict:
    case = store().get_case(recall_id) or {"recall_id": recall_id, "opened_at": _now(), "pulled": {}, "notices": {}}
    case["run_id"] = run_id
    return case


def status(case: dict, pack: dict) -> dict:
    pull_ids = [v["item_id"] for v in pack["pull_list"]]
    agencies = [n["agency_id"] for n in pack["agency_notices"]]
    to_pull = [i for i in pull_ids if i not in case["pulled"]]
    to_check = [v["item_id"] for v in pack["human_checks"]]
    to_send = [a for a in agencies if not case["notices"].get(a, {}).get("sent_at")]
    to_hear = [a for a in agencies if a not in to_send and not case["notices"].get(a, {}).get("replied_at")]
    if case.get("closed_at"):
        state = "closed"
    else:
        state = "pulling" if to_pull else "checking" if to_check else "notifying" if to_send else "waiting" if to_hear else "ready"
    return {"state": state, "text": STATUS_TEXT[state], "to_pull": to_pull, "to_check": to_check,
            "to_send": to_send, "to_hear": to_hear, "pulled": len(pull_ids) - len(to_pull), "pull_total": len(pull_ids),
            "agencies": len(agencies), "replied": len(agencies) - len(to_send) - len(to_hear)}


def _trace(run_id: str) -> str:
    return f"{os.getenv('LOG_DIR', 'logs')}/trace-{run_id}.jsonl"


def _save(case: dict, pack: dict, who: str, action: str, summary: str) -> dict:
    case["updated_at"] = _now()
    st = status(case, pack)
    case["status"] = st["state"]
    store().put_case(case["recall_id"], case)
    ingest().ingest("case", {**case, "case_id": case["recall_id"], "source_ref": f"recorded by {who}"}, "case_id",
                    pack["run_id"], agent="Person")
    log_step(pack["run_id"], "Person", "case", {}, _trace(pack["run_id"]), action=action, output_summary=f"{who}: {summary}; case now {st['text'].lower()}")
    return {"case": case, "status": st}


def mark_pulled(case: dict, pack: dict, item_id: str, pulled: bool, who: str) -> dict:
    if item_id not in [v["item_id"] for v in pack["pull_list"]]:
        raise LookupError(f"{item_id} is not on the pull list")
    if pulled:
        case["pulled"][item_id] = {"by": who, "at": _now()}
    else:
        case["pulled"].pop(item_id, None)
    return _save(case, pack, who, "mark_pulled", f"{item_id} {'pulled' if pulled else 'not pulled'}")


def mark_notice(case: dict, pack: dict, agency_id: str, event: str, who: str, remaining: float | None = None) -> dict:
    if agency_id not in [n["agency_id"] for n in pack["agency_notices"]]:
        raise LookupError(f"{agency_id} has no notice in this case")
    entry = case["notices"].setdefault(agency_id, {})
    if event == "sent":
        entry.update(sent_at=_now(), sent_by=who)
        summary = f"notice to {agency_id} sent"
    elif event == "replied":
        if not entry.get("sent_at"):
            raise ValueError("record the notice as sent before its reply")
        entry.update(replied_at=_now(), replied_by=who, remaining=remaining)
        summary = f"{agency_id} replied" + (f", {remaining:g} units still on hand" if remaining is not None else "")
    else:
        raise ValueError("event must be sent or replied")
    return _save(case, pack, who, f"notice_{event}", summary)


def close(case: dict, pack: dict, who: str) -> dict:
    st = status(case, pack)
    if st["state"] != "ready":
        raise ValueError(f"cannot close yet: {st['text'].lower()}")
    case.update(closed_at=_now(), closed_by=who)
    return _save(case, pack, who, "close_case", "case closed")
