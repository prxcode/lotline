import os
import sys
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("lotline")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    logger.propagate = False

_REDACT = {"contact_email", "contact_phone", "contact_name", "address", "notes"}


def _sanitize(data: dict) -> dict:
    return {k: ("[redacted]" if k in _REDACT else v) for k, v in data.items()}


def log_step(
    run_id: str,
    agent: str,
    event: str,
    data: dict,
    trace_path: str | None = None,
    *,
    action: str = "",
    input_summary: str = "",
    output_summary: str = "",
    target_agent: str = "",
    confidence: float | None = None,
    retry_count: int = 0,
    status: str = "success",
) -> dict:
    entry = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": run_id,
        "agent_name": agent,
        "event": event,
        "action": action or event,
        "input_summary": input_summary,
        "output_summary": output_summary,
        "target_agent": target_agent,
        "retry_count": retry_count,
        "status": status,
        **_sanitize(data),
    }
    if confidence is not None:
        entry["confidence"] = confidence
    # Readable line on stdout; the full record goes to logs/trace-<run_id>.jsonl
    arrow = f" -> {target_agent}" if target_agent else ""
    text = output_summary or input_summary or action or event
    level = logging.WARNING if status in ("error", "degraded") else logging.INFO
    logger.log(level, f"run_id={run_id} {agent} {event}{arrow} [{status}] {text}"[:400])
    if trace_path:
        with open(trace_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    return entry
