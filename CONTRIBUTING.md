# Contributing

## Setup

```bash
git clone <repo-url>
cd lotline-dev
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt   # Windows
source .venv/bin/activate && pip install -r requirements.txt  # Mac/Linux
copy .env.example .env
```

## Running

```bash
# Web app and API on http://localhost:8000
python -m uvicorn api.main:app --reload --port 8000

# CLI (fastest feedback loop)
python run.py --recall RC-2024-001 --mode sample

# Accuracy check; run before every PR
python eval.py --mode sample
```

The web app in `static/` is plain HTML, CSS, and JavaScript served by FastAPI. There is no build step.

## Adding a new agent

1. Create `agents/your_agent.py` with `run_your_agent(state: dict) -> dict`
2. Add it as a node and wire its edges in `graph/lotline_graph.py`, and add it to `AGENTS`
3. If it calls the LLM, use `call_llm(..., sample=state.get("sample_mode"))` and add an offline stand-in to `_SAMPLE` in `llm/client.py`
4. Write what it finds through `world_model.ingest()` so later agents can read it
5. Add it to `AGENT_ORDER` in `static/app.js` so it shows in the pipeline

## Code rules

- Single-line comments only, and only when the why is non-obvious
- No emoji anywhere
- Secrets go in `.env` only, never in code
- Model output never sets lot ranges, UPCs, or a final CONFIRMED or NO_MATCH verdict; rules in `agents/rules.py` do
- Every agent calls `log_step` at start and done
- Render untrusted text in the web app with `textContent`, never `innerHTML`

## Branching

- `main` — stable, demo-ready
- `feature/...` — new work; merge via PR
- Never force-push main
