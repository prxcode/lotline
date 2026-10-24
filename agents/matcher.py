from zetaris.client import find_stock, get_distinct_brands, shipped_totals
from agents.base import log_step
from agents.criteria import search_stem, is_ambiguous, upc_keys as upc_keys_of
from agents.resolve import resolve_brands, tokens
from world_model import ingest, entity_id


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _search_terms(criteria: dict, known_brands: list[str]) -> tuple[dict, list[str], list[str]]:
    brands = {}
    for line in criteria["lines"]:
        b = line["brand"]
        found = resolve_brands(b, criteria["aliases"].get(b, []) + line["other_names"],
                               criteria["learned"].get(b, []), known_brands)
        for name, how in found.items():
            if name not in brands or how["score"] > brands[name]["score"]:
                brands[name] = how
    # The shortest core of each UPC is a substring of however the warehouse stored it
    upc_keys = sorted({min(upc_keys_of(u), key=len) for line in criteria["lines"] for u in line["upcs"] if upc_keys_of(u)})
    # Product-name words catch rows with no brand, or the brand typed into the name
    words = {search_stem(line["terms"]["head"]) for line in criteria["lines"] if line["terms"]["head"]}
    for b in criteria["brands"]:
        words |= {w for w in tokens(b) if len(w) >= 5}
    return brands, upc_keys, sorted(words)


def run_matcher(state: dict) -> dict:
    run_id = state["run_id"]
    criteria = state["match_criteria"]
    sources = state["source_map"]
    revision = state.get("revision_count", 0)
    trace_path = state.get("trace_path")
    seen = {c["item_id"] for c in state.get("candidates", [])}

    brands, upc_keys, words = _search_terms(criteria, get_distinct_brands(sources))
    log_step(run_id, "Matcher", "start", {}, trace_path,
             action="federated_sql_search",
             input_summary=f"Searching {sources['stock']['table']} for {len(brands)} brand spelling(s), "
                           f"{len(upc_keys)} UPC(s) and product words {', '.join(words)}",
             target_agent="Critic", retry_count=revision)

    rows = find_stock(sources, list(brands), upc_keys, words)
    received = "quantity_received" in sources["stock"]["mapping"]
    shipped = shipped_totals(sources, [r["item_id"] for r in rows]) if received else {}

    candidates = []
    for row in rows:
        lot = str(row.get("lot_code") or "")
        brand = row.get("brand") or ""
        on_hand = max(0.0, _num(row.get("quantity_received")) - shipped.get(row["item_id"], 0.0)) if received else _num(row.get("quantity_on_hand"))
        flags = (["ambiguous_lot"] if is_ambiguous(lot) else []) + ([] if row.get("upc") else ["no_upc"]) + ([] if brand else ["no_brand"])
        candidates.append({
            "candidate_id": f"cand_{row['item_id']}",
            "item_id": row["item_id"],
            "brand": brand,
            "brand_match": {**brands.get(brand, {"method": "name or UPC", "score": 0.0}), "recorded_as": brand},
            "product": row.get("product") or "",
            "lot_code": lot,
            "upc": row.get("upc") or None,
            "best_by": row.get("best_by") or "",
            "quantity": on_hand,
            "quantity_received": _num(row.get("quantity_received")) if received else None,
            "shipped": shipped.get(row["item_id"], 0.0) if received else None,
            "unit": row.get("unit") or "",
            "location": row.get("location") or "",
            "source_type": row.get("source") or "",
            "received_date": row.get("received_date") or "",
            "notes": row.get("notes") or "",
            "source_table": sources["stock"]["table"],
            "source_ref": row["source_ref"],
            "flags": flags,
        })

    wm = ingest()
    wm.ingest_many("candidate", candidates, "candidate_id", run_id, agent="Matcher")
    recall_node = entity_id("recall", run_id, criteria["recall_id"])
    for c in candidates:
        wm.relate(run_id, entity_id("candidate", run_id, c["candidate_id"]), "may_be", recall_node, "Matcher")

    other = sorted(n for n, b in brands.items() if b["method"] != "exact")
    new = [c["item_id"] for c in candidates if c["item_id"] not in seen]
    summary = f"{len(rows)} stock rows to judge"
    if other:
        summary += f"; other brand spellings in the data: {', '.join(other)}"
    if revision:
        summary += f"; {len(new)} new since last round"
    log_step(run_id, "Matcher", "done", {"brands_searched": sorted(brands)}, trace_path,
             action="emit_candidates", output_summary=summary,
             target_agent="Critic", confidence=0.90, retry_count=revision)

    rows_in_table = next((x["rows"] for x in state.get("sources", []) if x["table_name"] == sources["stock"]["table"]), None)
    search = {"table": sources["stock"]["table"], "records": rows_in_table, "brands": sorted(brands),
              "upcs": upc_keys, "words": words}
    return {"candidates": candidates, "searched_brands": sorted(brands), "search": search, "revision_instructions": None}
