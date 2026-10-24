from datetime import datetime, timezone
from world_model.store import WorldStoreService, stable_id


class IngestPipelineService:
    def __init__(self, store: WorldStoreService):
        self._store = store

    def ingest(self, entity_type: str, raw: dict, key_field: str, run_id: str, agent: str = "") -> str:
        if key_field not in raw or raw[key_field] in (None, ""):
            raise ValueError(f"{entity_type} missing required field: {key_field}")
        entity_id = stable_id(entity_type, f"{run_id}:{raw[key_field]}")
        existing = self._store.get(entity_type, entity_id) or {}
        now = datetime.now(timezone.utc).isoformat()
        # Provenance: which agent wrote each version, and which source record it came from
        provenance = [*existing.get("_provenance", []),
                      {"agent": agent, "at": now, "source": raw.get("source_ref") or ""}]
        enriched = {
            **existing,
            **raw,
            "run_id": run_id,
            "_stable_id": entity_id,
            "_ingested_at": existing.get("_ingested_at") or now,
            "_revisions": existing.get("_revisions", -1) + 1,
            "_provenance": provenance,
        }
        self._store.upsert(entity_type, entity_id, enriched)
        return entity_id

    def ingest_many(self, entity_type: str, rows: list[dict], key_field: str, run_id: str, agent: str = "") -> list[str]:
        return [self.ingest(entity_type, r, key_field, run_id, agent) for r in rows]

    def relate(self, run_id: str, src: str, relation: str, dst: str, agent: str = ""):
        self._store.relate(run_id, src, relation, dst, agent)
