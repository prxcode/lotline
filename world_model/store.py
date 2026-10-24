import os
import json
import sqlite3
import hashlib
import threading
from pathlib import Path
from datetime import datetime, timezone


def event_log_path(run_id: str) -> Path:
    return Path(os.getenv("LOG_DIR", "logs")) / f"world-{run_id}.jsonl"


def stable_id(entity_type: str, key: str) -> str:
    return f"{entity_type}_{hashlib.sha256(key.encode()).hexdigest()[:12]}"


class WorldStoreService:
    def __init__(self, db_path: str | None = None):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path or os.getenv("DB_PATH", "lotline.db"), check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                entity_type TEXT NOT NULL,
                entity_id   TEXT NOT NULL,
                run_id      TEXT,
                data        TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                PRIMARY KEY (entity_type, entity_id)
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_run ON entities (run_id, entity_type)")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS relations (
                run_id   TEXT NOT NULL,
                src      TEXT NOT NULL,
                relation TEXT NOT NULL,
                dst      TEXT NOT NULL,
                agent    TEXT,
                PRIMARY KEY (run_id, src, relation, dst)
            )
        """)
        # One case per recall: what people did about it (pulled, notices sent and answered, closed)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS cases (
                recall_id  TEXT PRIMARY KEY,
                data       TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        # Brand spellings a person approved; they outlive any single run
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS learned_aliases (
                brand       TEXT NOT NULL,
                alias       TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                run_id      TEXT,
                PRIMARY KEY (brand, alias)
            )
        """)
        self._conn.commit()

    def upsert(self, entity_type: str, entity_id: str, data: dict):
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO entities VALUES (?, ?, ?, ?, ?)",
                (entity_type, entity_id, data.get("run_id"), json.dumps(data, default=str), ts),
            )
            self._conn.commit()
        last = (data.get("_provenance") or [{}])[-1]
        self._emit(data.get("run_id"), {"event": "entity.upserted", "entity_type": entity_type, "entity_id": entity_id,
                                        "revision": data.get("_revisions", 0), "agent": last.get("agent", ""),
                                        "source": last.get("source", "")})

    def get(self, entity_type: str, entity_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM entities WHERE entity_type=? AND entity_id=?", (entity_type, entity_id)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def for_run(self, run_id: str, entity_type: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM entities WHERE run_id=? AND entity_type=? ORDER BY entity_id", (run_id, entity_type)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def counts(self, run_id: str) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT entity_type, COUNT(*) FROM entities WHERE run_id=? GROUP BY entity_type", (run_id,)
            ).fetchall()
        return dict(rows)

    def relate(self, run_id: str, src: str, relation: str, dst: str, agent: str = ""):
        with self._lock:
            cur = self._conn.execute("INSERT OR IGNORE INTO relations VALUES (?, ?, ?, ?, ?)", (run_id, src, relation, dst, agent))
            self._conn.commit()
        if cur.rowcount:
            self._emit(run_id, {"event": "relation.added", "src": src, "relation": relation, "dst": dst, "agent": agent})

    def _emit(self, run_id: str | None, event: dict):
        # Append-only event log, one file per run, so every belief can be traced to the write that made it
        if not run_id:
            return
        path = event_log_path(run_id)
        path.parent.mkdir(exist_ok=True)
        line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "run_id": run_id, **event}, default=str)
        with self._lock, open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def neighbors(self, run_id: str, node: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT src, relation, dst, agent FROM relations WHERE run_id=? AND (src=? OR dst=?)", (run_id, node, node)
            ).fetchall()
        return [{"src": r[0], "relation": r[1], "dst": r[2], "agent": r[3]} for r in rows]

    def relations(self, run_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT src, relation, dst, agent FROM relations WHERE run_id=? ORDER BY relation, src", (run_id,)
            ).fetchall()
        return [{"src": r[0], "relation": r[1], "dst": r[2], "agent": r[3]} for r in rows]

    def approve_alias(self, brand: str, alias: str, run_id: str | None = None):
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO learned_aliases VALUES (?, ?, ?, ?)", (brand, alias, ts, run_id))
            self._conn.commit()

    def remove_alias(self, brand: str, alias: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM learned_aliases WHERE brand=? AND alias=?", (brand, alias))
            self._conn.commit()
        return cur.rowcount > 0

    def learned_aliases(self, brand: str | None = None) -> list[dict]:
        sql, args = "SELECT brand, alias, approved_at, run_id FROM learned_aliases", ()
        if brand:
            sql, args = sql + " WHERE lower(brand)=lower(?)", (brand,)
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY brand, alias", args).fetchall()
        return [{"brand": r[0], "alias": r[1], "approved_at": r[2], "run_id": r[3]} for r in rows]

    def get_case(self, recall_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT data FROM cases WHERE recall_id=?", (recall_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def put_case(self, recall_id: str, data: dict):
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO cases VALUES (?, ?, ?)", (recall_id, json.dumps(data, default=str), ts))
            self._conn.commit()

    def all_cases(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM cases").fetchall()
        return [json.loads(r[0]) for r in rows]
