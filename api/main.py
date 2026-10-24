import os
import re
import json
import threading
from pathlib import Path
from typing import Literal
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator, model_validator
from dotenv import load_dotenv

load_dotenv()

from graph.lotline_graph import run_recall, new_run_id, trace_path_for, AGENTS  # noqa: E402
from llm.client import LLMError, live_available, sample_mode_default, models, route, power_status, test_connection  # noqa: E402
from zetaris.client import list_recalls, get_recall, get_catalog, backend_name, check_backend, fallback_reason, DataError, default_source_map, search_stock, search_shipments, list_agencies  # noqa: E402
from world_model import store, context, event_log_path  # noqa: E402
from agents.resolution import record_reading  # noqa: E402
from agents import case as cases  # noqa: E402
from agents.record import render as render_record  # noqa: E402
from agents.screen import screen_all, invalidate as invalidate_screen  # noqa: E402

STATIC = Path(__file__).resolve().parent.parent / "static"
ASSETS = Path(__file__).resolve().parent.parent / "assets"
MAX_RUNS = 200

app = FastAPI(title="Lotline", description="Multi-agent recall tracing for food banks", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",")],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_runs: dict[str, dict] = {}
_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=4)


def _check_api_key(key: str | None = Security(_api_key_header)):
    expected = os.getenv("API_KEY")
    if expected and key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9\-_]", "", value or "")
    if not cleaned or len(cleaned) > 64:
        raise ValueError("invalid id")
    return cleaned


class TraceRequest(BaseModel):
    recall_id: str = "R-001"
    mode: Literal["sample", "live"] | None = None
    # Same shape as input_examples/*.json: {"recall_id": ..., "options": {"sample_mode": true}}
    options: dict = {}

    @model_validator(mode="after")
    def mode_from_options(self):
        if self.mode is None and "sample_mode" in self.options:
            self.mode = "sample" if self.options["sample_mode"] else "live"
        return self

    @field_validator("recall_id")
    @classmethod
    def sanitize(cls, v: str) -> str:
        return _safe_id(v)


class ReadingRequest(BaseModel):
    lot_code: str | None = None
    best_by: str | None = None
    upc: str | None = None
    read_by: str | None = None

    @field_validator("lot_code", "best_by", "upc", "read_by")
    @classmethod
    def short(cls, v: str | None) -> str | None:
        v = " ".join((v or "").split())
        if len(v) > 80:
            raise ValueError("must be 80 characters or fewer")
        return v or None


class CaseAction(BaseModel):
    run_id: str
    by: str | None = None
    item_id: str | None = None
    pulled: bool = True
    agency_id: str | None = None
    event: Literal["sent", "replied"] | None = None
    remaining: float | None = None

    @field_validator("by")
    @classmethod
    def who(cls, v: str | None) -> str | None:
        v = " ".join((v or "").split())[:60]
        return v or None


class AliasRequest(BaseModel):
    brand: str
    alias: str
    run_id: str | None = None

    @field_validator("brand", "alias")
    @classmethod
    def clean(cls, v: str) -> str:
        v = " ".join((v or "").split())
        if not v or len(v) > 80:
            raise ValueError("must be 1 to 80 characters")
        return v


def _sample_flag(req: TraceRequest) -> bool:
    if req.mode == "live" and not live_available():
        raise HTTPException(status_code=400, detail="Live mode needs MODEL_API_KEY (or NVIDIA_API_KEY) in .env")
    return sample_mode_default() if req.mode is None else req.mode == "sample"


def _error(code: int, kind: str, message: str, recoverable: bool) -> JSONResponse:
    return JSONResponse(status_code=code, content={"status": "error", "detail": message,
                                                  "error": {"type": kind, "message": message, "recoverable": recoverable}})


def _store_run(run_id: str, data: dict):
    with _lock:
        _runs[run_id] = data
        while len(_runs) > MAX_RUNS:
            _runs.pop(next(iter(_runs)))


def _background(run_id: str, recall_id: str, sample: bool):
    try:
        result = run_recall(recall_id, sample_mode=sample, run_id=run_id)
    except (LLMError, DataError) as exc:
        result = {"run_id": run_id, "status": "error", "error": str(exc)}
    except Exception as exc:
        result = {"run_id": run_id, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
    _store_run(run_id, {**_runs.get(run_id, {}), **result})


@app.get("/", include_in_schema=False)
def index():
    # no-cache so a browser always picks up the current page and its versioned CSS and JS
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "lotline"}


@app.get("/api/config", dependencies=[Security(_check_api_key)])
def config():
    return {
        "default_mode": "sample" if sample_mode_default() else "live",
        "live_available": live_available(),
        "models": models(),
        "model_route": route(),
        "power": power_status(),
        "data_backend": backend_name(),
        "data_fallback": fallback_reason(),
        "dataset": os.getenv("DATA_DIR", "data") if backend_name() == "local" else "Zetaris",
        "agents": AGENTS,
        "sources": get_catalog(),
    }


@app.get("/api/recalls", dependencies=[Security(_check_api_key)])
def recalls():
    check_backend()
    return list_recalls()


@app.get("/api/recalls/screen", dependencies=[Security(_check_api_key)])
def recalls_screen(refresh: bool = False):
    # Rules-only quick screen of every notice; opening a recall runs the full agent trace
    check_backend()
    return screen_all(refresh)


def _search_text(q: str) -> str:
    q = " ".join((q or "").split())
    if len(q) > 80:
        raise HTTPException(status_code=400, detail="Search must be 80 characters or fewer")
    return q


@app.get("/api/stock", dependencies=[Security(_check_api_key)])
def stock(q: str = "", limit: int = 200):
    # Read-only view of the warehouse records, through the same data layer the agents use
    check_backend()
    rows = search_stock(default_source_map(), _search_text(q), max(1, min(limit, 500)))
    return {"rows": rows, "backend": backend_name()}


@app.get("/api/shipments", dependencies=[Security(_check_api_key)])
def shipments(q: str = "", limit: int = 200):
    check_backend()
    rows = search_shipments(default_source_map(), _search_text(q), max(1, min(limit, 500)))
    return {"rows": rows, "backend": backend_name()}


@app.get("/api/agencies", dependencies=[Security(_check_api_key)])
def agencies():
    check_backend()
    return {"rows": list_agencies(default_source_map()), "backend": backend_name()}


@app.get("/api/recalls/{recall_id}", dependencies=[Security(_check_api_key)])
def recall_by_id(recall_id: str):
    row = get_recall(_safe_id(recall_id))
    if not row:
        raise HTTPException(status_code=404, detail=f"Recall {recall_id} not found")
    return row


@app.post("/run", dependencies=[Security(_check_api_key)])
def run_sync(req: TraceRequest):
    try:
        result = run_recall(req.recall_id, sample_mode=_sample_flag(req))
    except LookupError as exc:
        return _error(404, "NotFound", str(exc), recoverable=False)
    except (LLMError, DataError) as exc:
        return _error(502, type(exc).__name__, str(exc), recoverable=True)
    _store_run(result["run_id"], result)
    return result


@app.post("/api/runs", status_code=202, dependencies=[Security(_check_api_key)])
def run_async(req: TraceRequest):
    if not get_recall(req.recall_id):
        raise HTTPException(status_code=404, detail=f"Recall {req.recall_id} not found")
    sample = _sample_flag(req)
    run_id = new_run_id()
    trace_path_for(run_id)
    _store_run(run_id, {"run_id": run_id, "status": "running", "recall_id": req.recall_id,
                        "mode": "sample" if sample else "live"})
    _pool.submit(_background, run_id, req.recall_id, sample)
    return {"run_id": run_id, "status": "running"}


@app.get("/api/runs", dependencies=[Security(_check_api_key)])
def runs():
    with _lock:
        items = list(_runs.values())
    return [
        {
            "run_id": r["run_id"],
            "status": r.get("status"),
            "recall_id": r.get("output", {}).get("recall_id") or r.get("recall_id"),
            "pull_count": len(r.get("output", {}).get("pull_list", [])),
            "mode": r.get("output", {}).get("mode") or r.get("mode"),
            "execution_time_seconds": r.get("execution_time_seconds"),
        }
        for r in reversed(items)
    ]


@app.get("/api/runs/{run_id}", dependencies=[Security(_check_api_key)])
def run_by_id(run_id: str):
    run = _runs.get(_safe_id(run_id))
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.get("/api/runs/{run_id}/trace", dependencies=[Security(_check_api_key)])
def trace(run_id: str):
    path = Path(trace_path_for(_safe_id(run_id)))
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@app.post("/api/power/test", dependencies=[Security(_check_api_key)])
def power_test():
    from llm.providers import meterless_status
    meterless_status(refresh=True)
    return {"status": power_status(), "results": test_connection()}


def _case_run(run_id: str) -> dict | None:
    run = _runs.get(_safe_id(run_id))
    return run if run and run.get("status") == "success" else None


@app.get("/api/cases", dependencies=[Security(_check_api_key)])
def list_cases():
    return [{"recall_id": c["recall_id"], "status": c.get("status", "pulling")} for c in store().all_cases()]


@app.get("/api/cases/{recall_id}", dependencies=[Security(_check_api_key)])
def get_case(recall_id: str, run_id: str):
    run = _case_run(run_id)
    if not run:
        return _error(404, "NotFound", "Run not found; trace the recall again", recoverable=True)
    case = cases.load(_safe_id(recall_id), run["run_id"])
    return {"case": case, "status": cases.status(case, run["output"])}


@app.post("/api/cases/{recall_id}/{action}", dependencies=[Security(_check_api_key)])
def case_action(recall_id: str, action: Literal["pulled", "notice", "close"], req: CaseAction):
    run = _case_run(req.run_id)
    if not run:
        return _error(404, "NotFound", "Run not found; trace the recall again", recoverable=True)
    who = req.by or "a volunteer"
    try:
        with _lock:
            case = cases.load(_safe_id(recall_id), run["run_id"])
            if action == "pulled":
                return cases.mark_pulled(case, run["output"], _safe_id(req.item_id or ""), req.pulled, who)
            if action == "notice":
                return cases.mark_notice(case, run["output"], _safe_id(req.agency_id or ""), req.event or "", who, req.remaining)
            return cases.close(case, run["output"], who)
    except LookupError as exc:
        return _error(404, "NotFound", str(exc), recoverable=False)
    except ValueError as exc:
        return _error(409, "NotReady", str(exc), recoverable=True)


@app.get("/api/cases/{recall_id}/record", response_class=HTMLResponse, dependencies=[Security(_check_api_key)])
def case_record(recall_id: str, run_id: str):
    run = _case_run(run_id)
    if not run:
        return HTMLResponse("<p>Run not found; trace the recall again.</p>", status_code=404)
    case = cases.load(_safe_id(recall_id), run["run_id"])
    return render_record(run["output"], case, cases.status(case, run["output"]), trace(run["run_id"]))


@app.post("/api/runs/{run_id}/items/{item_id}/reading", dependencies=[Security(_check_api_key)])
def record_item_reading(run_id: str, item_id: str, req: ReadingRequest):
    run = _runs.get(_safe_id(run_id))
    if not run or run.get("status") != "success":
        return _error(404, "NotFound", "Run not found or not finished; trace the recall again", recoverable=True)
    try:
        with _lock:
            result = record_reading(run["output"], _safe_id(item_id), req.model_dump(exclude={"read_by"}), req.read_by or "",
                                    trace_path_for(run["run_id"]))
    except LookupError as exc:
        return _error(404, "NotFound", str(exc), recoverable=False)
    except ValueError as exc:
        return _error(400, "BadReading", str(exc), recoverable=True)
    return {**result, "output": run["output"]}


# World model control plane (Meterless World Model engine): query what the agents believed and why

@app.get("/api/runs/{run_id}/world", dependencies=[Security(_check_api_key)])
def world(run_id: str):
    ctx = context().build(_safe_id(run_id))
    if not ctx["entity_counts"]:
        raise HTTPException(status_code=404, detail="No world model state for this run")
    return ctx


@app.get("/api/runs/{run_id}/world/events", dependencies=[Security(_check_api_key)])
def world_events(run_id: str):
    path = event_log_path(_safe_id(run_id))
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@app.get("/api/runs/{run_id}/world/node/{node_id}", dependencies=[Security(_check_api_key)])
def world_node(run_id: str, node_id: str):
    return store().neighbors(_safe_id(run_id), _safe_id(node_id))


@app.get("/api/aliases", dependencies=[Security(_check_api_key)])
def aliases(brand: str | None = None):
    return store().learned_aliases(brand)


@app.post("/api/aliases", status_code=201, dependencies=[Security(_check_api_key)])
def approve_alias(req: AliasRequest):
    # A person confirms that this spelling is the brand; later runs treat it as a known spelling
    store().approve_alias(req.brand, req.alias, req.run_id)
    invalidate_screen()
    return {"brand": req.brand, "alias": req.alias, "status": "approved"}


@app.delete("/api/aliases", dependencies=[Security(_check_api_key)])
def remove_alias(brand: str, alias: str):
    invalidate_screen()
    if not store().remove_alias(brand, alias):
        raise HTTPException(status_code=404, detail="Alias not found")
    return {"brand": brand, "alias": alias, "status": "removed"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")))
