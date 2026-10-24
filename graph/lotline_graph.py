import time
import uuid
from pathlib import Path
from typing import TypedDict
from langgraph.graph import StateGraph, END
from agents.base import log_step
from agents.triage import run_triage
from agents.scout import run_scout
from agents.matcher import run_matcher
from agents.critic import run_critic
from agents.tracer import run_tracer
from agents.reporter import run_reporter
from llm.client import sample_mode_default
from zetaris.client import get_recall, check_backend

AGENTS = [
    {"name": "Triage", "role": "Turns each recalled product line into checkable criteria: UPCs, lot codes, best-by dates"},
    {"name": "SourceScout", "role": "Explores the data layer and maps each table and column to what it means"},
    {"name": "Matcher", "role": "Finds stock by brand spelling, UPC and product name, and works out what is still on hand"},
    {"name": "Critic", "role": "Judges every hit against each product line; Nemotron can escalate, never clear"},
    {"name": "Tracer", "role": "Follows flagged stock into shipments to partner agencies"},
    {"name": "Reporter", "role": "Builds the pull list, hand checks, and agency notices"},
]


class LotlineState(TypedDict):
    run_id: str
    recall_id: str
    recall: dict
    sample_mode: bool
    match_criteria: dict
    sources: list
    source_map: dict
    mapping_method: str
    data_gaps: list
    searched_brands: list
    search: dict
    candidates: list
    verdicts: list
    distributions: list
    agency_map: dict
    action_pack: dict
    revision_count: int
    needs_revision: bool
    revision_instructions: str | None
    trace_path: str | None


def _route_after_critic(state: LotlineState) -> str:
    return "matcher" if state.get("needs_revision") and state.get("revision_count", 0) <= 2 else "tracer"


def build_graph():
    g = StateGraph(LotlineState)
    for name, fn in [("triage", run_triage), ("scout", run_scout), ("matcher", run_matcher),
                     ("critic", run_critic), ("tracer", run_tracer), ("reporter", run_reporter)]:
        g.add_node(name, fn)
    g.set_entry_point("triage")
    g.add_edge("triage", "scout")
    g.add_edge("scout", "matcher")
    g.add_edge("matcher", "critic")
    g.add_conditional_edges("critic", _route_after_critic, {"matcher": "matcher", "tracer": "tracer"})
    g.add_edge("tracer", "reporter")
    g.add_edge("reporter", END)
    return g.compile()


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:8]}"


def trace_path_for(run_id: str) -> str:
    Path("logs").mkdir(exist_ok=True)
    return f"logs/trace-{run_id}.jsonl"


def run_recall(recall_id: str, sample_mode: bool | None = None, run_id: str | None = None) -> dict:
    check_backend()
    recall = get_recall(recall_id)
    if not recall:
        raise LookupError(f"Recall {recall_id} not found")

    run_id = run_id or new_run_id()
    trace_path = trace_path_for(run_id)
    sample = sample_mode_default() if sample_mode is None else sample_mode

    t0 = time.time()
    try:
        result = build_graph().invoke({
            "run_id": run_id, "recall_id": recall["recall_id"], "recall": recall, "sample_mode": sample,
            "match_criteria": {}, "sources": [], "source_map": {}, "mapping_method": "", "data_gaps": [],
            "searched_brands": [], "search": {}, "candidates": [], "verdicts": [], "distributions": [],
            "agency_map": {}, "action_pack": {}, "revision_count": 0, "needs_revision": False,
            "revision_instructions": None, "trace_path": trace_path,
        })
    except Exception as exc:
        log_step(run_id, "Pipeline", "error", {}, trace_path, action="abort", output_summary=str(exc)[:300], status="error")
        raise

    return {
        "run_id": run_id,
        "status": "success",
        "output": result["action_pack"],
        "agents": AGENTS,
        "trace_id": run_id,
        "log_file": trace_path,
        "execution_time_seconds": round(time.time() - t0, 2),
    }
