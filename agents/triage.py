from llm.client import call_llm
from agents.base import log_step
from agents.criteria import parse_line
from world_model import ingest, store


def _strings(values, limit: int = 8) -> list[str]:
    if not isinstance(values, list):
        return []
    return [v.strip() for v in values if isinstance(v, str) and v.strip()][:limit]


def run_triage(state: dict) -> dict:
    run_id = state["run_id"]
    recall = state["recall"]
    trace_path = state.get("trace_path")
    lines = [parse_line(row) for row in recall.get("lines", [])]
    brands = list(dict.fromkeys(ln["brand"] for ln in lines if ln["brand"])) or [recall.get("brand", "")]

    log_step(run_id, "Triage", "start", {}, trace_path,
             action="parse_recall",
             input_summary=f"Recall {recall['recall_id']}: {len(lines)} product line(s) from {', '.join(brands)}",
             target_agent="SourceScout")

    ingest().ingest("recall", {k: v for k, v in recall.items() if k != "lines"}, "recall_id", run_id, agent="Triage")

    # Codes come from the notice, parsed by rules; the model only suggests how a brand may be written
    aliases, learned, models = {}, {}, set()
    for brand in brands:
        products = "; ".join(ln["label"] for ln in lines if ln["brand"] == brand)
        system = ("You are a food safety specialist at a food bank. List the most likely ways this brand is written in a "
                  "warehouse inventory by volunteers: abbreviations, initials, distributor shorthand, common typos. At most 8. "
                  "Skip legal suffixes like Inc or LLC.")
        user = (f"Brand: {brand}\nProducts: {products}\nRecalling company: {recall.get('company', '')}\n"
                'Return JSON: {"brand_aliases": [str]}')
        result, model = call_llm("triage", system, user, sample=state.get("sample_mode"), payload={"brand": brand})
        models.add(model)
        aliases[brand] = [a for a in _strings(result.get("brand_aliases")) if a.lower() != brand.lower()]
        learned[brand] = [a["alias"] for a in store().learned_aliases(brand)]

    criteria = {
        "recall_id": recall["recall_id"],
        "lines": lines,
        "brands": brands,
        "aliases": aliases,
        "learned": learned,
        "brand_aliases": list(dict.fromkeys([*brands, *(a for v in aliases.values() for a in v)])),
        "learned_aliases": [a for v in learned.values() for a in v],
    }

    kinds = []
    for ln in lines:
        lot = {"all": "all lots", "list": f"{len(ln['lots']['specs'])} lot code(s)", "none": "no lot codes"}[ln["lots"]["kind"]]
        kinds.append(f"{ln['label']}: {len(ln['upcs'])} UPC(s), {lot}" + (f", {len(ln['dates']['specs'])} best-by date(s)" if ln["dates"]["specs"] else ""))
    learned_all = criteria["learned_aliases"]
    log_step(run_id, "Triage", "done", {"model": ", ".join(sorted(models))}, trace_path,
             action="emit_match_criteria",
             output_summary="; ".join(kinds[:4]) + (f"; +{len(kinds) - 4} more lines" if len(kinds) > 4 else "")
                            + (f". Learned spellings: {', '.join(learned_all)}" if learned_all else ""),
             target_agent="SourceScout", confidence=0.98)

    return {"match_criteria": criteria}
