import json
from zetaris.client import get_catalog, list_tables, describe, sample_rows, backend_name, fallback_reason
from zetaris.schema import SYNONYMS, validate
from llm.client import run_tools, LLMError
from agents.base import log_step
from world_model import ingest

_RELEVANCE = {"stock": 0.95, "shipments": 0.9, "contacts": 0.8, "notice_lines": 0.6, "notices": 0.4}
_NEEDED = ("stock", "shipments", "contacts")

_TOOLS = [
    {"type": "function", "function": {"name": "list_tables", "description": "List every table the data layer can reach",
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "describe_table", "description": "Column names of one table",
                                      "parameters": {"type": "object", "properties": {"table": {"type": "string"}}, "required": ["table"]}}},
    {"type": "function", "function": {"name": "sample_rows", "description": "Up to 3 rows of one table, to see what the columns hold",
                                      "parameters": {"type": "object", "properties": {"table": {"type": "string"}}, "required": ["table"]}}},
]


def _ask_model(catalog: list[dict]) -> tuple[dict, str, list]:
    fields = {r: list(SYNONYMS[r]) for r in _NEEDED}
    system = ("You map a food bank's data for a recall trace. Use the tools to look at the tables, then decide which table "
              "holds each role and which column holds each field. Roles and fields: " + json.dumps(fields) + ". "
              "For stock, use quantity_received if the quantity is what arrived (shipments are subtracted later) and "
              "quantity_on_hand if it is what is on the shelf now. Leave out fields with no matching column. "
              'Finish with JSON only: {"stock": {"table": str, "mapping": {field: column}}, "shipments": {...}, "contacts": {...}}')
    handlers = {"list_tables": lambda: list_tables(), "describe_table": lambda table: describe(table),
                "sample_rows": lambda table: sample_rows(table, 3)}
    return run_tools(system, "Map the data for the recall trace.", _TOOLS, handlers)


def run_scout(state: dict) -> dict:
    run_id = state["run_id"]
    trace_path = state.get("trace_path")

    fell_back = fallback_reason()
    log_step(run_id, "SourceScout", "start", {}, trace_path,
             action="query_catalog", input_summary=f"Discover data sources through the {backend_name()} data layer",
             target_agent="Matcher")
    if fell_back:
        log_step(run_id, "SourceScout", "fallback", {}, trace_path, action="use_local_copy",
                 output_summary=f"Zetaris unreachable, continuing on the local copy: {fell_back}", status="degraded")

    catalog = get_catalog()
    by_table = {t["table"]: t for t in catalog}
    rules_map = {}
    for t in sorted(catalog, key=lambda t: -t["rows"]):
        if t["role"] != "other":
            rules_map.setdefault(t["role"], {"table": t["table"], "mapping": t["mapping"]})

    sources, how, model, calls = dict(rules_map), "column names", "rules", []
    if not state.get("sample_mode"):
        # Nemotron explores the catalog with tools; its mapping is used only if every column checks out
        try:
            proposed, model, calls = _ask_model(catalog)
            problems = []
            for role in _NEEDED:
                p = proposed.get(role) or {}
                # A field mapped to null is a field the model left out
                p["mapping"] = {k: v for k, v in (p.get("mapping") or {}).items() if v}
                cols = by_table.get(p.get("table"), {}).get("columns", [])
                bad = validate(role, cols, p.get("mapping") or {}) if cols else [f"unknown table {p.get('table')!r}"]
                problems += [f"{role}: {b}" for b in bad]
            if problems:
                how = f"column names (model mapping rejected: {'; '.join(problems)[:200]})"
            else:
                sources.update({r: {"table": proposed[r]["table"], "mapping": proposed[r]["mapping"]} for r in _NEEDED})
                agree = all(proposed[r]["table"] == rules_map.get(r, {}).get("table") and proposed[r]["mapping"] == rules_map.get(r, {}).get("mapping") for r in _NEEDED)
                how = "Nemotron via tools, checked against the columns" + ("" if agree else " (differs from the name-based mapping)")
        except (LLMError, ValueError, KeyError, TypeError) as exc:
            how = f"column names (model unavailable: {str(exc)[:120]})"

    missing = [r for r in _NEEDED + ("notices", "notice_lines") if r not in sources]
    if missing:
        raise LookupError(f"No table found for: {', '.join(missing)}")

    records = [{"source_id": f"src_{t['table']}", "table_name": t["table"], "role": t["role"], "rows": t["rows"],
                "columns": t["columns"], "relevance_score": _RELEVANCE.get(t["role"], 0.1), "reason": t["description"]}
               for t in catalog]
    ingest().ingest_many("source", records, "source_id", run_id, agent="SourceScout")

    used = ", ".join(f"{sources[r]['table']} as {r} ({by_table[sources[r]['table']]['rows']} rows)" for r in _NEEDED)
    log_step(run_id, "SourceScout", "done", {"model": model, "tool_calls": calls, "mapping": {r: sources[r] for r in _NEEDED}},
             trace_path, action="emit_source_map", output_summary=f"{used}; mapped by {how}",
             target_agent="Matcher", confidence=0.95)

    return {"sources": records, "source_map": sources, "mapping_method": how,
            "data_gaps": [f"Zetaris unreachable; used the local copy ({fell_back})"] if fell_back else []}
