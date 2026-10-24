"""The close-out record: one printable page an auditor can read without opening the app."""
from html import escape


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value).replace("T", " ") if isinstance(value, str) and len(value) > 15 and value[10:11] == "T" else str(value)


def _rows(items: list[dict], cols: list[tuple[str, str]]) -> str:
    if not items:
        return '<p class="none">None.</p>'
    head = "".join(f"<th>{escape(label)}</th>" for label, _ in cols)
    body = "".join("<tr>" + "".join(f"<td>{escape(_cell(v.get(key)))}</td>" for _, key in cols) + "</tr>"
                   for v in items)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render(pack: dict, case: dict, st: dict, trace: list[dict]) -> str:
    r = pack["recall"]
    lines = [{"product": f"{ln.get('brand', '')} {ln.get('product', '')} {ln.get('size', '')}".strip(), "upc": ln.get("upc"),
              "codes": ln.get("lot_codes"), "best_by": ln.get("best_by")} for ln in r.get("lines", [])]
    pulled = [{**v, "pulled": f"{case['pulled'][v['item_id']]['by']}, {case['pulled'][v['item_id']]['at'][:16]}"
               if v["item_id"] in case["pulled"] else "not yet"} for v in pack["pull_list"]]
    readings = [{"item_id": u["item_id"], "reading": ", ".join(f"{k.replace('_', ' ')} {x}" for k, x in u["reading"].items()),
                 "by": u["by"], "at": u["at"][:16], "change": f"{u['from']} to {u['to']}"} for u in pack.get("updates", [])]
    notices = []
    for n in pack["agency_notices"]:
        c = case["notices"].get(n["agency_id"], {})
        notices.append({"agency": n["agency_name"], "items": "; ".join(f"{i['quantity']:g} {i['unit']} {i['item_id']}" for i in n["items"]),
                        "sent": f"{c.get('sent_by', '')} {c.get('sent_at', '')[:16]}".strip() or "not sent",
                        "reply": (f"{c.get('replied_at', '')[:16]}" + (f", {c['remaining']:g} still on hand" if c.get("remaining") is not None else ""))
                        if c.get("replied_at") else "no reply"})
    steps = [{"time": e.get("timestamp", "")[11:19], "agent": e.get("agent_name"), "what": e.get("output_summary") or e.get("input_summary") or e.get("action")}
             for e in trace]
    closed = f"Closed by {escape(case['closed_by'])} at {escape(case['closed_at'][:16])} UTC." if case.get("closed_at") else f"Status: {escape(st['text'])}."
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Close-out record {escape(r['recall_id'])}</title>
<style>body{{font:13px/1.45 system-ui,sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#1b1a17}}h1{{font-size:20px;margin:0}}
h2{{font-size:14px;margin:22px 0 6px;border-bottom:1px solid #1b1a17;padding-bottom:3px}}table{{border-collapse:collapse;width:100%}}
th,td{{text-align:left;padding:4px 8px 4px 0;border-bottom:1px solid #ddd;vertical-align:top}}th{{font-size:11px;text-transform:uppercase;color:#666}}
.meta{{color:#555}}.none{{color:#777}}@media print{{body{{margin:0}}}}</style></head><body>
<h1>Recall close-out record: {escape(r['brand'])} {escape(r['product'])}</h1>
<p class="meta">{escape(r.get('source', ''))} notice announced {escape(r.get('announced_date', ''))} by {escape(r.get('company', ''))}. Reason: {escape(r['reason'])}.
{('Notice: ' + escape(r['notice_url']) + '.') if r.get('notice_url') else ''}<br>Case opened {escape(case['opened_at'][:16])} UTC. {closed}
Trace {escape(pack['run_id'])}, {escape(pack['mode'])} mode, {escape(pack['model'])}.</p>
<h2>What the notice recalls</h2>{_rows(lines, [("Product", "product"), ("UPC", "upc"), ("Lot or product codes", "codes"), ("Best by", "best_by")])}
<h2>Summary</h2><p><b>Now:</b> {len(pack['pull_list'])} item(s) to pull ({pack['total_units_to_pull']:g} units), {len(pack['human_checks'])} still to check by hand,
{len(pack['agency_notices'])} agenc{'y' if len(pack['agency_notices']) == 1 else 'ies'} to notify, {len(pack['near_misses'])} checked and ruled out.</p>
<p class="meta">Briefing written when the recall was traced{', before the package readings below' if pack.get('updates') else ''}: {escape(pack['verdict_summary'])}</p>
<h2>Pulled from our shelves ({st['pulled']} of {st['pull_total']})</h2>{_rows(pulled, [("Item", "item_id"), ("Product", "product"), ("Lot", "lot_code"), ("Qty", "quantity"), ("Location", "location"), ("Why", "rationale"), ("Source", "source_ref"), ("Pulled", "pulled")])}
<h2>Still to be checked by a person</h2>{_rows(pack['human_checks'], [("Item", "item_id"), ("Product", "product"), ("Location", "location"), ("What to check", "rationale")])}
<h2>Package readings</h2>{_rows(readings, [("Item", "item_id"), ("Reading", "reading"), ("By", "by"), ("At (UTC)", "at"), ("Decision", "change")])}
<h2>Agencies that received it</h2>{_rows(notices, [("Agency", "agency"), ("Shipped items", "items"), ("Notice sent", "sent"), ("Reply", "reply")])}
<h2>Checked and ruled out</h2>{_rows(pack['near_misses'], [("Item", "item_id"), ("Product", "product"), ("Lot", "lot_code"), ("Why it stays", "rationale")])}
<h2>How it was decided</h2>{_rows(steps, [("Time (UTC)", "time"), ("Agent", "agent"), ("Step", "what")])}
</body></html>"""
