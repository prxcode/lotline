from typing import TypedDict
from world_model.store import WorldStoreService


class WorldModelContext(TypedDict):
    run_id: str
    recall: dict
    sources_searched: list[dict]
    candidates: list[dict]
    critiques: list[dict]
    shipments: list[dict]
    relations: list[dict]
    entity_counts: dict[str, int]
    manifest: dict


class ContextBuilderService:
    def __init__(self, store: WorldStoreService):
        self._store = store

    def build(self, run_id: str) -> WorldModelContext:
        recalls = self._store.for_run(run_id, "recall")
        relations = self._store.relations(run_id)
        counts = self._store.counts(run_id)
        if relations:
            counts["relation"] = len(relations)
        return WorldModelContext(
            run_id=run_id,
            recall=recalls[0] if recalls else {},
            sources_searched=self._store.for_run(run_id, "source"),
            candidates=self._store.for_run(run_id, "candidate"),
            critiques=self._store.for_run(run_id, "critique"),
            shipments=self._store.for_run(run_id, "shipment"),
            relations=relations,
            entity_counts=counts,
            # Which agent wrote each kind of entity, so a consumer knows where every part of the context came from
            manifest={"recall": "Triage", "source": "SourceScout", "candidate": "Matcher", "critique": "Critic",
                      "shipment": "Tracer", "relation": "Matcher, Critic, Tracer"},
        )
