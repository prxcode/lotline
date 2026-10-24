import re
from datetime import datetime, timezone
from llm.client import call_llm, _sample_reporter
from agents.base import log_step
from zetaris.client import count_agencies
from world_model import context

_CAND_FIELDS = ("brand", "brand_match", "product", "lot_code", "upc", "best_by", "quantity", "quantity_received",
                "shipped", "unit", "location", "source_type", "received_date", "source_ref")
_RELATED = {"exact", "learned", "alias", "notice", "in_name", "fuzzy"}


def codes_text(recall: dict) -> str:
    parts = []
    for ln in recall.get("lines", []):
        lots = ln.get("lot_codes") or ""
        code = "all lots" if lots.upper() == "ALL" else f"codes {lots}" if lots else ""
        bb = f"best by {ln['best_by']}" if ln.get("best_by") else ""
        upc = f"UPC {ln['upc']}" if ln.get("upc") else ""
        parts.append(f"{ln.get('product', '')} {ln.get('size', '')}".strip() + ": " + ", ".join(x for x in (upc, code, bb) if x))
    return "; ".join(parts)


def _numbers(text: str) -> set[float]:
    return {float(n) for n in re.findall(r"\d+(?:\.\d+)?", text or "")}


def _check_briefing(text, facts: dict, grounding: str) -> str | None:
    """Why the model's briefing can't be used, or None. Every number must appear in the facts."""
    if not isinstance(text, str) or not text.strip():
        return "empty answer"
    invented = _numbers(text) - _numbers(f"{facts} {grounding}")
    if invented:
        return "numbers not in the data: " + ", ".join(f"{n:g}" for n in sorted(invented))
    return None


def _draft_notice(agency: dict, recall: dict) -> str:
    remove = [i for i in agency["items"] if i.get("item_verdict") == "CONFIRMED_MATCH"]
    hold = [i for i in agency["items"] if i.get("item_verdict") != "CONFIRMED_MATCH"]

    def lines(items):
        return "\n".join(f"  - {i['product']}, lot {i['lot_code'] or 'not recorded'}: {i['quantity']:g} {i['unit']} sent "
                         f"{i['distributed_date']} (our ref {i['dist_id']})" for i in items)

    body = [
        f"Subject: Food recall action needed: {recall['brand']} {recall['product']}",
        "",
        f"Dear {agency['contact_name'] or agency['agency_name']},",
        "",
        f"{recall.get('company') or recall['brand']} has recalled {recall['brand']} {recall['product']} ({recall['reason']}). "
        f"Recalled: {codes_text(recall)}. Our records show some of it reached you.",
    ]
    if remove:
        body += ["", "Please remove from distribution and set aside now:", lines(remove)]
    if hold:
        body += ["", "Please hold these until we confirm the lot code (we will call you today):", lines(hold)]
    body += [
        "",
        "Do not discard anything yet. Reply to this email once the product is set aside, and tell us how many "
        "units you still have and how many already went to clients.",
        "",
        "Food Safety Team",
    ]
    return "\n".join(body)


def run_reporter(state: dict) -> dict:
    run_id = state["run_id"]
    recall = state["recall"]
    trace_path = state.get("trace_path")
    distributions = state.get("distributions", [])
    candidates = {c["item_id"]: c for c in state.get("candidates", [])}

    log_step(run_id, "Reporter", "start", {}, trace_path,
             action="build_action_pack",
             input_summary=f"{len(state['verdicts'])} verdicts, {len(distributions)} shipments")

    world = context().build(run_id)

    verdicts = [{**{k: candidates.get(v["item_id"], {}).get(k) for k in _CAND_FIELDS}, **v} for v in state["verdicts"]]
    pull_list = [v for v in verdicts if v["action"] == "pull_and_notify"]
    human_checks = [v for v in verdicts if v["action"] == "human_verify"]
    # Same-brand stock first: it is what a blanket "pull the brand" response would have thrown out
    near_misses = sorted((v for v in verdicts if v["verdict"] in ("NEAR_MISS", "NO_MATCH")),
                         key=lambda v: (v.get("brand_match", {}).get("method") not in _RELATED, v["item_id"]))
    related = [v for v in verdicts if v.get("brand_match", {}).get("method") in _RELATED]
    agency_notices = [{**a, "draft_message": _draft_notice(a, recall)} for a in state.get("agency_map", {}).values()]

    def units(items):
        return sum(float(v.get("quantity") or 0) for v in items)

    # What a blanket response (pull every case of the brand, email every agency) would have cost
    impact = {
        "brand_units_found": units(related),
        "units_to_pull": units(pull_list),
        "units_to_check": units(human_checks),
        "units_kept_in_use": units(v for v in related if v["action"] in ("monitor", "clear")),
        "agencies_total": count_agencies(state["source_map"]),
        "agencies_to_notify": len(agency_notices),
    }

    facts = {
        "items_to_pull_now": len(pull_list),
        "units_to_pull_now_in_warehouse": impact["units_to_pull"],
        "items_to_check_by_hand": len(human_checks),
        "units_set_aside_for_checking": impact["units_to_check"],
        "same_brand_units_staying_on_shelf": impact["units_kept_in_use"],
        "units_already_shipped_out": sum(float(d.get("quantity") or 0) for d in distributions),
        "shipments_already_sent": len(distributions),
        "agencies_that_received_it": len(agency_notices),
    }
    system = ("You are the food safety lead at a food bank. Write a short briefing for the warehouse manager. "
              "Plain words, no jargon, lead with what to do first. Only the listed items are pulled: stock of the "
              "same brand with other codes stays on the shelf. Use only numbers from the facts, each with "
              "the meaning its name gives it. Quantities are units still in the warehouse.")
    user = (f"Recall: {recall['brand']} {recall['product']} ({recall['reason']}). {codes_text(recall)}\n"
            f"Facts: {facts}\n"
            f"Items to check by hand: {[(v['item_id'], v['rationale']) for v in human_checks]}\n"
            'Return JSON: {"verdict_summary": "2 to 3 sentences"}')
    legacy = {"pull_count": len(pull_list), "check_count": len(human_checks), "agency_count": len(agency_notices),
              "dist_count": len(distributions), "cases": impact["units_to_pull"]}
    summary, model = call_llm("reporter", system, user, payload=legacy, sample=state.get("sample_mode"))
    text = summary.get("verdict_summary")
    grounding = " ".join(str(recall.get(k, "")) for k in recall) + " " + " ".join(
        f"{v['item_id']} {v['rationale']}" for v in human_checks)
    if state.get("sample_mode") or model.startswith("rules"):
        briefing_source = "template"
    else:
        problem = _check_briefing(text, facts, grounding)
        briefing_source = f"{model} (numbers checked)" if not problem else f"template (model briefing rejected: {problem})"
        if problem:
            text = None
    if not text:
        text = _sample_reporter(legacy)["verdict_summary"]
    if not pull_list and not human_checks:
        # "Not affected" is an answer only if it says where we looked
        sr = state.get("search", {})
        brands = ", ".join(state.get("match_criteria", {}).get("brands", [])) or recall.get("brand", "")
        found = ", ".join(b for b in sr.get("brands") or [] if b) or "no spelling of it in our data"
        looked = [f"the brand {brands} ({found})"]
        if sr.get("upcs"):
            looked.append(f"UPCs containing {', '.join(sr['upcs'])}")
        if sr.get("words"):
            looked.append(f"product names containing {', '.join(sr['words'])}")
        ruled = f" {len(near_misses)} similar record{'s' if len(near_misses) != 1 else ''} checked and ruled out." if near_misses else ""
        text = (f"Not affected. Searched {sr.get('table', 'stock')} ({sr.get('records', '?')} records) for "
                + "; ".join(looked) + f".{ruled} Nothing to pull and no agency to notify.")
        briefing_source = "template"

    confidences = [v["confidence"] for v in pull_list + human_checks]
    action_pack = {
        "run_id": run_id,
        "recall_id": recall["recall_id"],
        "recall": recall,
        "recall_summary": f"{recall['brand']} {recall['product']}: {recall['reason']}",
        "verdict_summary": text.strip(),
        "briefing_source": briefing_source,
        "pull_list": pull_list,
        "human_checks": human_checks,
        "near_misses": near_misses,
        "distributed_items": distributions,
        "agency_notices": agency_notices,
        "total_units_to_pull": impact["units_to_pull"],
        "impact": impact,
        "confidence_overall": round(sum(confidences) / len(confidences), 2) if confidences else 1.0,
        "sources": state.get("sources", []),
        "match_criteria": state.get("match_criteria", {}),
        "source_map": state.get("source_map", {}),
        "mapping_method": state.get("mapping_method", ""),
        "data_gaps": state.get("data_gaps", []) + ([f"Nemotron unavailable; the rules decided alone ({model})"]
                                                   if model.startswith("rules (Nemotron unavailable") else []),
        "searched_brands": state.get("searched_brands", []),
        "search": state.get("search", {}),
        "revision_rounds": state.get("revision_count", 0),
        "mode": "sample" if state.get("sample_mode") else "live",
        "model": model,
        "world_model": world["entity_counts"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    log_step(run_id, "Reporter", "done", {"model": model, "briefing_source": briefing_source}, trace_path,
             action="emit_action_pack",
             output_summary=f"{len(pull_list)} to pull, {len(human_checks)} to check, {len(agency_notices)} agencies to notify"
                            + (f"; {briefing_source}" if "rejected" in briefing_source else ""),
             confidence=action_pack["confidence_overall"])

    return {"action_pack": action_pack}
