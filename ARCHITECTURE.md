# Architecture

## Overview

Lotline is a six-agent LangGraph pipeline behind a FastAPI server, which also serves the web app. It follows the Data / Build / Power layers of the organizers' Participant Setup Guide: data comes through Zetaris (or a local DuckDB stand-in that runs the same SQL), the model is Nemotron through NVIDIA's API (the Meterless Runtime route is implemented but was not run in this build), and the code was built with Cursor and Claude Code. Agents share typed state through the graph and write what they find to a Meterless-style world model. Deterministic rules decide every verdict.

```
Browser (static/)  ──HTTP──>  FastAPI (api/main.py)
                                   |
                       run_recall()  graph/lotline_graph.py
                                   |
   Triage -> SourceScout -> Matcher -> Critic --(spelling found in data, max 2)--> Matcher
                                         |
                                Tracer -> Reporter -> action pack
                                   |                     |
          zetaris/client.py (SQL)  |      world_model/ (SQLite): entities, provenance,
   Zetaris REST  or  DuckDB on CSV |      relationships, learned spellings
                                   |
          llm/ -> Meterless Runtime, then NVIDIA API (Nemotron); tool loop for SourceScout
                           or offline sample mode
```

## Agents

| Agent | Reads | Writes | Model use |
|---|---|---|---|
| Triage | recall header and product lines, learned spellings | `match_criteria`: parsed lines, aliases per brand | Brand spellings only. Codes, UPCs and dates are parsed by `agents/criteria.py` |
| SourceScout | catalog: tables, columns, sample rows | `source_map`: role -> table and column mapping | Live: tool loop (`list_tables`, `describe_table`, `sample_rows`) proposes the mapping; `zetaris/schema.validate` checks it, else name-based mapping is used |
| Matcher | distinct brands, then stock by brand, UPC core or product-name stem; shipped totals | `candidates` with on-hand quantity and source line | None |
| Critic | candidates, parsed lines | `verdicts`, optional extra spellings | Reviews verdicts; may escalate to a person, never confirm or clear |
| Tracer | shipments joined to contacts | `distributions`, `agency_map` | None |
| Reporter | everything plus world-model context | `action_pack` with `impact` | Briefing; every number checked against the facts |

## Recall criteria

`recall_products` holds one row per product line, as FDA notices print them. `agents/criteria.py` parses each line:

| Field | Parsed into |
|---|---|
| `upc` | UPC cores. Notices drop leading zeros or check digits (`4800073265`), and cases carry GTIN-14 codes with a packaging digit; cores make them comparable |
| `lot_codes` | `ALL`, nothing, or a list of codes and ranges (`0188 262 20:06 - 0188 262 22:05`). Codes are compared without spaces and dashes, so `B-35360` = `B35360` and `0188265 09:45` falls in its range |
| `best_by` | A list of dates or date ranges. Notices often list dates, not a range |
| `product` | Naming words with weights (descriptors such as "organic" count half) and a head noun (what the product is) |
| `other_names` | Names the notice prints besides the brand: company, item-number prefixes |

## Decision rules

`agents/rules.py` checks an item against each line separately and keeps the most serious result:

1. UPC differs from the line's UPCs: not this product.
2. Without a UPC match: a clearly different brand, a missing head noun, or a stated pack weight that differs from the line rules the item out.
3. Codes: every code the line gives (lot, best-by) is matched, missing, unreadable or outside. Any unreadable, missing or conflicting code goes to a person. All outside rules it out (near miss if a lot is within 5).
4. All codes match: pull if the UPC matches; or the line has no UPC and the brand is strong; or the brand is strong and either the name covers 75% of the line's words with a specific lot, or a specific lot and the best-by date both match. Otherwise check by hand.

Strong brands: exact, learned (approved by a person), alias (named by the model), notice (printed on the notice), or written into the product name when the brand field is empty. A brand that only resembles the notice's is never confirmed without a UPC.

The Critic merges the model's opinion: it may escalate PROBABLE, NEAR_MISS or NO_MATCH to HUMAN_CHECK; any other disagreement is logged and overridden. A suggested spelling triggers a Matcher re-run only if it exists in the data and was not searched.

## The coordinator's workflow

- **Inbox screen** (`agents/screen.py`): for every notice, the same parsing, search and rules as a trace, without model calls or traces; cached for a minute and cleared when a spelling is approved or a reading recorded. It answers "does this affect us?" before anyone opens a recall.
- **Step-by-step recall work** (`static/`): pull from shelves, check packages, tell agencies, finish. Case progress comes from `agents/case.py`, so a recall closes only when every pull, reading and notice is done.
- **Hand-check readings** (`agents/resolution.py`): a person records the lot code, best-by or UPC printed on the package. The rules decide again with the reading merged in; the item moves between Pull now, Check by hand and Ruled out; shipment flags, agency notices and impact numbers are recomputed; the reading is written to the World Model with the person as the agent and logged as a `Person -> Critic` handoff. A reading that still leaves the evidence unclear or contradictory keeps the item with a person.

## LangGraph state

```python
class LotlineState(TypedDict):
    run_id: str
    recall_id: str
    recall: dict                  # header plus product lines
    sample_mode: bool
    match_criteria: dict
    sources: list
    source_map: dict              # role -> {table, mapping}, from SourceScout
    mapping_method: str
    searched_brands: list
    candidates: list
    verdicts: list
    distributions: list
    agency_map: dict
    action_pack: dict
    revision_count: int
    needs_revision: bool
    revision_instructions: str | None
    trace_path: str | None
```

## World model (Meterless World Model engine)

Checked against the engine's implementation checklist in `tests/test_world_model.py`: content-hash stable IDs, idempotent ingest with revision counts, provenance with agent attribution, typed relationships, a context builder with a manifest of which agent wrote what, JSONL event log in `logs/`, and a real run whose Reporter reads the context.


| Service | Responsibility |
|---|---|
| `WorldStoreService` | SQLite entity store keyed by `(entity_type, entity_id)`, indexed by `run_id`; thread-safe |
| `IngestPipelineService` | Validate, derive a stable ID, merge, count revisions, append provenance (agent, time, source line) |
| Relationships | `candidate may_be recall`, `critique judges candidate`, `shipment shipped_from candidate`, `shipment delivered_to agency` |
| `ContextBuilderService` | Everything known about a run, used by the Reporter |
| Learned aliases | Spellings a person approved; read by Triage on every run |

## Data access

`zetaris/client.py` has two backends behind one interface:

- `DATA_BACKEND=zetaris`: one SQL statement per `POST {ZETARIS_REST_URL}/sql-editor/sqls/run`, with a bearer key, `X-Org-ID` and a fresh UUID `X-Request-ID` (setup guide 1.9). Values are inlined as quoted literals. Logical table names map to Zetaris tables through `ZETARIS_TABLES`. The endpoint returns rows as plain lists (as Zetaris's own `getting-started` helpers read them), so column names come from the query's own `SELECT ... AS` list. The three systems are PostgreSQL databases registered as JDBC data sources and addressed as `<datasource>.<table>` (`lotline_warehouse.inventory`, `lotline_distribution.distributions` and `.agencies`, `lotline_recalls.recalls` and `.recall_products`). Verified on a live Zetaris Platform on 23 Oct: the same answers as the local copy, about 5 s per search and about a minute per full check. Sums are done in Python because Zetaris pushes `CAST(... AS DOUBLE)` down to PostgreSQL, which has no `DOUBLE` type.
- `DATA_BACKEND=local`: DuckDB over every CSV in `DATA_DIR` (answer keys excluded), read as text so UPC leading zeros survive; `?` placeholders for values.

`zetaris/schema.py` assigns each table a role from its columns and maps columns to canonical fields by name, so `brand_as_entered`, `warehouse_location` and `quantity_received` work without code changes. Every row carries `source_ref`.

## Model providers (Power)

`llm/providers.py` builds the provider chain; `llm/client.py` tries each provider and each of its models in turn.

| Order | Provider | How it is found | Notes |
|---|---|---|---|
| 1 | **Meterless Runtime** (Nightshift; implemented, not run in this build) | `METERLESS_BASE_URL`, or a probe of `/healthz` on 127.0.0.1, localhost and [::1], ports 8088, 8090, 8787, 11434 | Bearer token from `METERLESS_RUNTIME_TOKEN` or the runtime's `proxy-token` file (`rt-local` rejected); `meterless-app: lotline` on every request, `meterless-scope: user` on chat; models discovered from `GET /v1/models` (401/403 = unauthorized, 200 = online); 180 s timeout |
| 2 | **NVIDIA API** | `NVIDIA_API_KEY` | `nemotron-3.5-lightning-30b-a3b`, then `nemotron-3.5-super-vl-120b-a12b`. On 22 Oct `nemotron-3-super-120b-a12b` was listed but returned 404, so it is no longer the default |
| 3 | Rules alone | always | The run continues with the same decisions; the report and UI say the model was unavailable |

`MODEL_PROVIDER=nvidia` puts NVIDIA first. Thinking is off by default so the token budget goes to the answer. Each trace line records the model and provider that answered.

Only item ID, brand, product, lot, UPC, best-by and source are sent to the model. Notes, shelf locations and contacts stay on the server.

## Trace format

One JSON line per agent step in `logs/trace-<run_id>.jsonl`, with contact fields and notes redacted. SourceScout's line includes the tool calls Nemotron made and the mapping used.

## API and web app

`POST /api/runs` starts a run on a worker thread; the web app polls the run and its trace and updates the agent track as events arrive. Several recalls can be checked at once, and the server keeps finished runs, which the app reuses (`GET /api/runs`) instead of running again; every recall gets the rules-only check on open, and the check with AI suggestions runs only on a click. `POST /run` is the synchronous version for scripts and judges. `/api/stock`, `/api/shipments` and `/api/agencies` give read-only views of the three systems through the same data layer the agents use. The web app is static HTML, CSS and JS rendered with `textContent`; CSV export escapes formula characters; the pull list and close-out record print from the page's own print area.

## Deployment

| Entry point | Command |
|---|---|
| Web app and API | `python -m uvicorn api.main:app --port 8000` |
| Docker | `docker build -t lotline . && docker run -p 8000:8000 --env-file .env lotline` |
| CLI | `python run.py --recall R-004 --mode sample` |
| Evaluation | `python eval.py` |
| Setup check | `python smoke_test.py` |
