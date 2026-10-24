from functools import lru_cache
from world_model.store import WorldStoreService, stable_id, event_log_path
from world_model.ingest import IngestPipelineService
from world_model.context import ContextBuilderService

__all__ = ["store", "ingest", "context", "entity_id", "event_log_path", "WorldStoreService"]


@lru_cache(maxsize=1)
def store() -> WorldStoreService:
    return WorldStoreService()


def ingest() -> IngestPipelineService:
    return IngestPipelineService(store())


def context() -> ContextBuilderService:
    return ContextBuilderService(store())


def entity_id(entity_type: str, run_id: str, key: str) -> str:
    return stable_id(entity_type, f"{run_id}:{key}")
