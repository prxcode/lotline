import json
from llm.client import call_llm
from agents.base import log_step
from agents.rules import evaluate, VERDICT_ACTION
from agents.resolve import norm, brand_similarity
from zetaris.client import get_distinct_brands, get_stock_items
from world_model import ingest, entity_id

_PROMPT_FIELDS = ("item_id", "brand", "product", "lot_code", "upc", "best_by", "source_type")
_REVIEW_MAX = 30


def _unsearched(proposed: list[str], searched: list[str], sources: dict) -> dict[str, str]:
    """Map proposed spellings to brands in the data that haven't been searched yet."""
    done = {norm(b) for b in searched}
    out = {}
    for alias in proposed:
        for brand in get_distinct_brands(sources):
            if norm(brand) not in done and (norm(brand) == norm(alias) or brand_similarity(brand, alias) >= 0.9):
                out.setdefault(alias, brand)
    return out


def _merge(rule: dict, model_v: dict | None) -> dict:
    # The model may escalate to a human, but never confirm or clear on its own
    final = dict(rule)
    final["source"] = "rules"
    if not model_v:
        return final
    final["model_note"] = str(model_v.get("rationale") or "")[:400]
    if model_v.get("verdict") == "HUMAN_CHECK" and rule["verdict"] in ("PROBABLE_MATCH", "NEAR_MISS", "NO_MATCH"):
        final.update(verdict="HUMAN_CHECK", action=VERDICT_ACTION["HUMAN_CHECK"], confidence=0.6,
                     rationale=f"{rule['rationale']}. Reviewer flagged for a person: {final['model_note']}",
                     source="rules+model escalation")
    elif model_v.get("verdict") and model_v.get("verdict") != rule["verdict"]:
        final["source"] = "rules (model disagreed, overridden)"
    return final


_EVIDENCE = ("brand", "product", "upc", "lot_code", "best_by")


def _verify_pulls(state: dict, candidates: list[dict], verdicts: list[dict]) -> list[dict]:
    """Before a pull is approved, re-read each record from the source and check the evidence still holds."""
    pulls = [v["item_id"] for v in verdicts if v["action"] == "pull_and_notify"]
    if not pulls:
        return verdicts
    fresh = get_stock_items(state["source_map"], pulls)
    seen = {c["item_id"]: c for c in candidates}
    changed = []
    for v in verdicts:
        if v["item_id"] not in pulls:
            continue
        now, then = fresh.get(v["item_id"]), seen[v["item_id"]]
        diff = [f for f in _EVIDENCE if now is None or (now.get(f) or "") != (then.get(f) or "")]
        v["rule_checks"] = v.get("rule_checks", []) + [{"rule": "source_recheck", "passed": not diff,
            "note": "Re-read from the source before approval: unchanged" if not diff else f"Changed since it was read: {', '.join(diff)}"}]
        if diff:
            changed.append(v["item_id"])
            v.update(verdict="HUMAN_CHECK", action=VERDICT_ACTION["HUMAN_CHECK"], confidence=0.5,
                     rationale=f"{v['rationale']}. The source record changed while tracing ({', '.join(diff)}); check it by hand")
    log_step(state["run_id"], "Critic", "verify", {}, state.get("trace_path"), action="verify_against_source",
             input_summary=f"Re-reading {len(pulls)} records about to be pulled",
             output_summary=f"{len(pulls) - len(changed)} unchanged" + (f"; {len(changed)} changed, sent to a person" if changed else ""),
             target_agent="Critic", status="success" if not changed else "needs_revision")
    return verdicts


def run_critic(state: dict) -> dict:
    run_id = state["run_id"]
    criteria = state["match_criteria"]
    candidates = state["candidates"]
    revision_count = state.get("revision_count", 0)
    trace_path = state.get("trace_path")

    log_step(run_id, "Critic", "start", {}, trace_path,
             action="audit_candidates",
             input_summary=f"Auditing {len(candidates)} candidates for {criteria['recall_id']}",
             retry_count=revision_count)

    rule_verdicts = [evaluate(criteria, c) for c in candidates]

    system = (
        "You are a food safety auditor reviewing automated recall matches at a food bank. For each item, give "
        "your verdict. Use HUMAN_CHECK when a label, product name, or pack size could plausibly be the recalled "
        "item but the evidence is incomplete. Keep each rationale under 25 words. Also list at most 3 brand "
        "spellings the search may have missed, or an empty list."
    )
    lines_view = [{"brand": ln["brand"], "product": ln["label"], "upcs": ln["upcs"], "lot_codes": ln["lots"]["source"],
                   "best_by": ln["dates"]["source"]} for ln in criteria["lines"]]
    # Items ruled out for a different brand or UPC need no second opinion
    review = [(c, r) for c, r in zip(candidates, rule_verdicts)
              if not (r["verdict"] == "NO_MATCH" and r["rationale"].startswith(("Different brand", "Different UPC")))][:_REVIEW_MAX]
    user = (
        f"Recalled product lines:\n{json.dumps(lines_view)}\n\n"
        f"Items with rule-based pre-check:\n"
        + json.dumps([{**{k: c.get(k) for k in _PROMPT_FIELDS}, "rule_verdict": r["verdict"]} for c, r in review])
        + '\n\nReturn JSON: {"verdicts": [{"item_id": str, "verdict": "CONFIRMED_MATCH|PROBABLE_MATCH|'
          'NEAR_MISS|NO_MATCH|HUMAN_CHECK", "rationale": str}], "missing_aliases": [str]}'
    )
    result, model = call_llm("critic", system, user, sample=state.get("sample_mode"),
                             payload={"criteria": criteria, "candidates": candidates})

    by_id = {v.get("item_id"): v for v in result.get("verdicts", []) if isinstance(v, dict)}
    verdicts = [_merge(r, by_id.get(r["item_id"])) for r in rule_verdicts]
    verdicts = _verify_pulls(state, candidates, verdicts)
    escalated = sum(1 for v in verdicts if v["source"] == "rules+model escalation")
    overridden = sum(1 for v in verdicts if v["source"].startswith("rules (model disagreed"))

    known = {a.lower() for a in criteria["brand_aliases"]}
    proposed = [a.strip() for a in result.get("missing_aliases") or []
                if isinstance(a, str) and a.strip() and a.strip().lower() not in known][:5]
    useful = _unsearched(proposed, state.get("searched_brands", []), state["source_map"])
    new_aliases = list(dict.fromkeys(useful.values()))
    needs_revision = bool(new_aliases) and revision_count < 2

    wm = ingest()
    wm.ingest_many("critique", [{**v, "round": revision_count} for v in verdicts], "item_id", run_id, agent="Critic")
    for v in verdicts:
        wm.relate(run_id, entity_id("critique", run_id, v["item_id"]), "judges",
                  entity_id("candidate", run_id, f"cand_{v['item_id']}"), "Critic")

    counts = {k: sum(1 for v in verdicts if v["verdict"] == k) for k in ("CONFIRMED_MATCH", "PROBABLE_MATCH", "HUMAN_CHECK")}
    summary = (f"{counts['CONFIRMED_MATCH']} confirmed, {counts['PROBABLE_MATCH']} probable, "
               f"{counts['HUMAN_CHECK']} need a person")
    if escalated or overridden:
        summary += f"; model escalated {escalated}, guardrail overrode {overridden}"
    if needs_revision:
        summary += f"; asking Matcher to re-search with {', '.join(new_aliases)}"
    elif proposed:
        summary += f"; reviewer suggested {', '.join(proposed)}, but no unsearched spelling like that is in the data"

    log_step(run_id, "Critic", "done", {"model": model, "proposed_aliases": proposed}, trace_path,
             action="request_revision" if needs_revision else "emit_verdicts",
             output_summary=summary,
             target_agent="Matcher" if needs_revision else "Tracer",
             confidence=0.95, retry_count=revision_count,
             status="revision_requested" if needs_revision else "success")

    out = {
        "verdicts": verdicts,
        "needs_revision": needs_revision,
        "revision_instructions": f"Add aliases: {', '.join(new_aliases)}" if needs_revision else None,
        "revision_count": revision_count + (1 if needs_revision else 0),
    }
    if needs_revision:
        aliases = {b: list(v) for b, v in criteria["aliases"].items()}
        for alias in new_aliases:
            brand = max(criteria["brands"], key=lambda b: brand_similarity(alias, b))
            aliases.setdefault(brand, []).append(alias)
        out["match_criteria"] = {**criteria, "aliases": aliases, "brand_aliases": criteria["brand_aliases"] + new_aliases}
    return out
