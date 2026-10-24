"""The Meterless World Model engine, checked against the items in its own implementation checklist."""
from world_model.store import WorldStoreService, stable_id
from world_model.ingest import IngestPipelineService
from world_model.context import ContextBuilderService


def engine():
    store = WorldStoreService(":memory:")
    return store, IngestPipelineService(store), ContextBuilderService(store)


def test_content_hash_stable_ids():
    assert stable_id("candidate", "run_a:cand_INV-1") == stable_id("candidate", "run_a:cand_INV-1")
    assert stable_id("candidate", "run_a:cand_INV-1") != stable_id("candidate", "run_b:cand_INV-1")


def test_ingest_is_idempotent_and_counts_revisions():
    store, ingest, _ = engine()
    first = ingest.ingest("candidate", {"candidate_id": "c1", "lot_code": "2412"}, "candidate_id", "run_a", agent="Matcher")
    again = ingest.ingest("candidate", {"candidate_id": "c1", "lot_code": "2412", "verdict": "pull"}, "candidate_id", "run_a", agent="Critic")
    assert first == again
    record = store.get("candidate", first)
    assert record["_revisions"] == 1 and record["verdict"] == "pull" and record["lot_code"] == "2412"
    assert store.counts("run_a") == {"candidate": 1}


def test_provenance_keeps_agent_attribution_and_source():
    store, ingest, _ = engine()
    eid = ingest.ingest("shipment", {"dist_id": "D-1", "source_ref": "distributions.csv line 4"}, "dist_id", "run_a", agent="Tracer")
    prov = store.get("shipment", eid)["_provenance"]
    assert prov[-1]["agent"] == "Tracer" and prov[-1]["source"] == "distributions.csv line 4"


def test_rejects_records_without_a_key():
    _, ingest, _ = engine()
    try:
        ingest.ingest("candidate", {"lot_code": "2412"}, "candidate_id", "run_a")
    except ValueError:
        return
    raise AssertionError("ingest accepted a record with no key")


def test_context_builder_returns_entities_relationships_and_manifest():
    store, ingest, context = engine()
    rid = ingest.ingest("recall", {"recall_id": "R-1"}, "recall_id", "run_a", agent="Triage")
    cid = ingest.ingest("candidate", {"candidate_id": "c1"}, "candidate_id", "run_a", agent="Matcher")
    ingest.relate("run_a", cid, "may_be", rid, "Matcher")
    ctx = context.build("run_a")
    assert ctx["recall"]["recall_id"] == "R-1" and len(ctx["candidates"]) == 1
    assert ctx["relations"] == [{"src": cid, "relation": "may_be", "dst": rid, "agent": "Matcher"}]
    assert ctx["manifest"]["candidate"] == "Matcher" and ctx["entity_counts"]["relation"] == 1


def test_a_real_run_writes_the_world_model_the_reporter_reads(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "data")
    from graph.lotline_graph import run_recall
    pack = run_recall("R-001", sample_mode=True)["output"]
    counts = pack["world_model"]
    # Every agent wrote through the pipeline, and the Reporter built its pack from that context
    assert counts["recall"] == 1 and counts["source"] >= 5
    assert counts["candidate"] == counts["critique"] > 0 and counts["shipment"] > 0 and counts["relation"] > 0
