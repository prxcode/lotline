from zetaris.client import get_shipments
from agents.base import log_step
from world_model import ingest, entity_id

_TRACE_VERDICTS = ("CONFIRMED_MATCH", "PROBABLE_MATCH", "HUMAN_CHECK")


def run_tracer(state: dict) -> dict:
    run_id = state["run_id"]
    verdicts = state["verdicts"]
    trace_path = state.get("trace_path")
    items = {c["item_id"]: c for c in state.get("candidates", [])}

    # Unclear items are traced too: if they turn out bad, the shipment is already known
    traced = {v["item_id"]: v["verdict"] for v in verdicts if v["verdict"] in _TRACE_VERDICTS}

    log_step(run_id, "Tracer", "start", {}, trace_path,
             action="trace_distributions",
             input_summary=f"Following {len(traced)} flagged items into {state['source_map']['shipments']['table']}",
             target_agent="Reporter")

    rows = []
    for r in get_shipments(state["source_map"], list(traced)):
        item = items.get(r["item_id"], {})
        rows.append({**r, "dist_id": r.get("ship_id"), "distributed_date": r.get("ship_date") or "",
                     "quantity": float(r.get("quantity") or 0), "unit": r.get("unit") or item.get("unit", ""),
                     "product": item.get("product", ""), "lot_code": item.get("lot_code", ""),
                     "item_verdict": traced.get(r["item_id"])})

    agency_map: dict[str, dict] = {}
    for row in rows:
        agency = agency_map.setdefault(row["agency_id"], {
            "agency_id": row["agency_id"],
            "agency_name": row.get("agency_name") or row["agency_id"],
            "contact_email": row.get("contact_email") or "",
            "contact_name": row.get("contact_name") or "",
            "items": [],
        })
        agency["items"].append(row)

    wm = ingest()
    wm.ingest_many("shipment", rows, "dist_id", run_id, agent="Tracer")
    for row in rows:
        node = entity_id("shipment", run_id, row["dist_id"])
        wm.relate(run_id, node, "shipped_from", entity_id("candidate", run_id, f"cand_{row['item_id']}"), "Tracer")
        wm.relate(run_id, node, "delivered_to", f"agency_{row['agency_id']}", "Tracer")

    log_step(run_id, "Tracer", "done", {}, trace_path,
             action="emit_distributions",
             output_summary=f"{len(rows)} shipments found going to {len(agency_map)} partner agencies",
             target_agent="Reporter", confidence=0.98)

    return {"distributions": rows, "agency_map": agency_map}
