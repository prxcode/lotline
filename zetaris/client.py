import os
import re
import csv
import uuid
import threading
from functools import lru_cache
from pathlib import Path
from zetaris.schema import detect_role, ROLE_TEXT


class DataError(RuntimeError):
    pass


_fallback = {"reason": None}


def backend_name() -> str:
    if _fallback["reason"]:
        return "local"
    return "zetaris" if os.getenv("DATA_BACKEND", "local").lower() == "zetaris" else "local"


def fallback_reason() -> str | None:
    return _fallback["reason"]


def _data_dir() -> str:
    return os.getenv("DATA_DIR", "data")


# Local stand-in: DuckDB over a folder of CSVs. Same SQL shape as the Zetaris route.

def _local_tables(data_dir: str) -> dict[str, Path]:
    # answer keys are for evaluation only, never a data source
    return {p.stem: p for p in sorted(Path(data_dir).glob("*.csv")) if not p.stem.startswith("answer_key")}


_local_lock = threading.Lock()


@lru_cache(maxsize=4)
def _local_db(data_dir: str, stamp: tuple):
    import duckdb
    con = duckdb.connect()
    for name, path in _local_tables(data_dir).items():
        # Loaded once per change to the files; all_varchar keeps UPC leading zeros and turns blank cells into NULL
        con.execute(f"CREATE TABLE \"{name}\" AS SELECT * FROM read_csv('{str(path).replace(chr(39), '')}', header=true, all_varchar=true)")
    return con


def _local_query(sql: str, params: list, data_dir: str) -> list[dict]:
    stamp = tuple((p.name, p.stat().st_mtime) for p in _local_tables(data_dir).values())
    con = _local_db(data_dir, stamp)
    # One connection, one query at a time, so concurrent runs do not interleave
    with _local_lock:
        # Values inlined through _literal, as on the Zetaris route; DuckDB's prepared statements cost ~200 ms each
        cur = con.execute(_inline(sql, params))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


# one SQL statement per POST to /sql-editor/sqls/run

def _literal(value) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _inline(sql: str, params: list) -> str:
    # The REST endpoint takes plain SQL, so values are quoted here; every value passes through _literal
    parts = sql.split("?")
    if len(parts) - 1 != len(params):
        raise DataError("parameter count does not match the SQL")
    return parts[0] + "".join(_literal(p) + rest for p, rest in zip(params, parts[1:]))


def _zetaris_query(sql: str, params: list) -> list[dict]:
    import requests
    url = os.getenv("ZETARIS_REST_URL", "http://localhost:8888/api/v1.0").rstrip("/") + "/sql-editor/sqls/run"
    key, org = os.getenv("ZETARIS_API_KEY"), os.getenv("ZETARIS_ORG_ID", "1")
    if not key:
        raise DataError("DATA_BACKEND=zetaris needs ZETARIS_API_KEY in .env")
    try:
        resp = requests.post(url, timeout=60, headers={
            "Authorization": f"Bearer {key}", "X-Org-ID": org, "X-Request-ID": str(uuid.uuid4()),
        }, json={"queryId": str(uuid.uuid4()), "sql": _inline(sql, params), "source": "SqlEditor",
                 "limit": int(os.getenv("ZETARIS_QUERY_LIMIT", "10000"))})
    except Exception as exc:
        raise DataError(f"Zetaris unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise DataError(f"Zetaris {resp.status_code}: {resp.text[:1200]}  sql: {_inline(sql, params)[:600]}")
    body = resp.json()
    data = (body.get("data") if isinstance(body, dict) else None) or []
    if data and isinstance(data[0], dict):
        return data
    # rows come back as plain lists, so take column names from the SELECT list
    cols = _aliases(sql)
    if cols is None:
        raise DataError(f"cannot name the columns of: {sql[:80]}")
    return [dict(zip(cols, row)) for row in data]


def _split_top(text: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in text:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    return parts + [cur]


def _aliases(sql: str) -> list[str] | None:
    if re.match(r"\s*DESCRIBE\b", sql, re.IGNORECASE):
        return ["col_name", "data_type", "comment"]
    m = re.match(r"\s*SELECT\s+(?:DISTINCT\s+)?(.*?)\s+FROM\s", sql, re.IGNORECASE | re.DOTALL)
    if not m:
        return ["value"] if re.match(r"\s*SELECT\s+\S+\s*$", sql, re.IGNORECASE) else None
    names = []
    for part in _split_top(m.group(1)):
        part = part.strip()
        if part == "*" or part.endswith(".*"):
            return None
        alias = re.search(r"\s+AS\s+([A-Za-z_]\w*)\s*$", part, re.IGNORECASE)
        names.append(alias.group(1) if alias else part.split(".")[-1].strip('`" '))
    return names


def _zetaris_tables() -> dict[str, str]:
    # ZETARIS_TABLES=inventory=lotline.inventory,distributions=lotline.distributions,...
    raw = os.getenv("ZETARIS_TABLES", "")
    pairs = [p.split("=", 1) for p in raw.split(",") if "=" in p]
    if not pairs:
        raise DataError("DATA_BACKEND=zetaris needs ZETARIS_TABLES (name=database.table, comma separated)")
    return {k.strip(): v.strip() for k, v in pairs}


def query(sql: str, params: list | None = None) -> list[dict]:
    params = params or []
    if backend_name() == "zetaris":
        return _zetaris_query(sql, params)
    return _local_query(sql, params, _data_dir())


def check_backend() -> str | None:
    """Probe Zetaris once per run; returns why it fell back to the local copy, or None."""
    _fallback["reason"] = None
    if os.getenv("DATA_BACKEND", "local").lower() != "zetaris":
        return None
    try:
        _zetaris_query("SELECT 1", [])
    except DataError as exc:
        if os.getenv("DATA_FALLBACK", "true").lower() != "true":
            raise
        _fallback["reason"] = str(exc)[:200]
    return _fallback["reason"]


def _ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.]+", name):
        raise DataError(f"unsafe identifier {name!r}")
    if backend_name() == "zetaris":
        return ".".join(f"`{p}`" for p in name.split("."))
    return f'"{name}"'


def _physical(table: str) -> str:
    return _zetaris_tables().get(table, table) if backend_name() == "zetaris" else table


def _columns(table: str) -> list[str]:
    if backend_name() == "zetaris":
        # DESCRIBE returns col_name first
        return [list(r.values())[0] for r in query(f"DESCRIBE {_ident(_physical(table))}") if list(r.values())[0]]
    with open(_local_tables(_data_dir())[table], newline="", encoding="utf-8") as f:
        return next(csv.reader(f))


def list_tables() -> list[str]:
    if backend_name() == "zetaris":
        return list(_zetaris_tables())
    return list(_local_tables(_data_dir()))


def describe(table: str) -> list[str]:
    if table not in list_tables():
        raise DataError(f"unknown table {table!r}")
    return _columns(table)


def sample_rows(table: str, n: int = 3) -> list[dict]:
    if table not in list_tables():
        raise DataError(f"unknown table {table!r}")
    cols = ", ".join(_ident(c) for c in _columns(table))
    return query(f"SELECT {cols} FROM {_ident(_physical(table))} LIMIT {int(min(n, 5))}")


def get_catalog() -> list[dict]:
    out = []
    for t in list_tables():
        cols = _columns(t)
        role, mapping = detect_role(cols)
        rows = query(f"SELECT COUNT(*) AS n FROM {_ident(_physical(t))}")[0]["n"]
        out.append({"table": t, "role": role, "description": ROLE_TEXT.get(role, "Not used"),
                    "rows": int(rows), "columns": cols, "mapping": mapping,
                    "location": _physical(t) if backend_name() == "zetaris" else f"{t}.csv"})
    return out


def default_source_map() -> dict:
    """role -> {table, mapping}, the largest table per role."""
    roles: dict = {}
    for t in sorted(get_catalog(), key=lambda t: -t["rows"]):
        if t["role"] != "other":
            roles.setdefault(t["role"], {"table": t["table"], "mapping": t["mapping"]})
    return roles


def _select(source: dict, fields: list[str]) -> str:
    m = source["mapping"]
    return ", ".join(f"{_ident(m[f])} AS {f}" for f in fields if f in m)


def _col(source: dict, field: str) -> str:
    return _ident(source["mapping"][field])


@lru_cache(maxsize=16)
def _lines(path: str, key: str, mtime: float) -> dict[str, int]:
    with open(path, newline="", encoding="utf-8") as f:
        return {row[key]: i for i, row in enumerate(csv.DictReader(f), start=2)}


def _cite(source: dict, key_field: str, rows: list[dict]) -> list[dict]:
    """Attach where each row came from: file and line locally, table and key on Zetaris."""
    table, key_col = source["table"], source["mapping"][key_field]
    if backend_name() == "local":
        path = _local_tables(_data_dir())[table]
        lines = _lines(str(path), key_col, os.path.getmtime(path))
        for r in rows:
            line = lines.get(r.get(key_field))
            r["source_ref"] = f"{path.name} line {line}" if line else path.name
    else:
        for r in rows:
            r["source_ref"] = f"{_physical(table)} {key_col}={r.get(key_field)}"
    return rows


# Recall notices

def list_recalls(sources: dict | None = None) -> list[dict]:
    sources = sources or default_source_map()
    n = sources["notices"]
    fields = ["recall_id", "recall_number", "source", "company", "brand", "product", "reason", "announced_date", "status", "notice_url"]
    rows = query(f"SELECT {_select(n, fields)} FROM {_ident(_physical(n['table']))}")
    lines = get_recall_lines(None, sources)
    for r in rows:
        r["lines"] = [ln for ln in lines if ln["recall_id"] == r["recall_id"]]
    return sorted(rows, key=lambda r: r.get("announced_date") or "", reverse=True)


def get_recall(recall_id: str, sources: dict | None = None) -> dict | None:
    rid = re.sub(r"[^A-Za-z0-9\-_]", "", recall_id or "")
    return next((r for r in list_recalls(sources) if r["recall_id"] == rid), None)


def get_recall_lines(recall_id: str | None, sources: dict) -> list[dict]:
    src = sources["notice_lines"]
    fields = ["recall_id", "line_id", "brand", "product", "size", "upc", "lot_codes", "best_by", "other_names"]
    sql = f"SELECT {_select(src, fields)} FROM {_ident(_physical(src['table']))}"
    params = []
    if recall_id:
        sql += f" WHERE {_col(src, 'recall_id')} = ?"
        params.append(recall_id)
    rows = query(sql, params)
    for i, r in enumerate(rows):
        r.setdefault("line_id", str(i + 1))
    return rows


# Stock, shipments, contacts

STOCK_FIELDS = ["item_id", "brand", "product", "upc", "lot_code", "best_by", "quantity_received", "quantity_on_hand",
                "unit", "location", "received_date", "source", "notes"]


def get_distinct_brands(sources: dict) -> list[str]:
    s = sources["stock"]
    if "brand" not in s["mapping"]:
        return []
    rows = query(f"SELECT DISTINCT {_col(s, 'brand')} AS brand FROM {_ident(_physical(s['table']))} WHERE {_col(s, 'brand')} IS NOT NULL")
    return sorted(r["brand"] for r in rows if r["brand"])


def get_stock_items(sources: dict, item_ids: list[str]) -> dict[str, dict]:
    """Fresh read of specific stock records, used to verify evidence before a pull is approved."""
    if not item_ids:
        return {}
    s = sources["stock"]
    sql = (f"SELECT {_select(s, STOCK_FIELDS)} FROM {_ident(_physical(s['table']))} "
           f"WHERE {_col(s, 'item_id')} IN ({', '.join('?' * len(item_ids))})")
    return {r["item_id"]: r for r in _cite(s, "item_id", query(sql, item_ids))}


def find_stock(sources: dict, brands: list[str], upc_keys: list[str], words: list[str]) -> list[dict]:
    """Rows matching one of the brands, UPC keys or product words."""
    s = sources["stock"]
    m = s["mapping"]
    conds, params = [], []
    if brands and "brand" in m:
        conds.append(f"lower({_col(s, 'brand')}) IN ({', '.join('?' * len(brands))})")
        params += [b.lower() for b in brands]
    if "upc" in m:
        for k in upc_keys:
            conds.append(f"{_col(s, 'upc')} LIKE ?")
            params.append(f"%{k}%")
    for w in words:
        conds.append(f"lower({_col(s, 'product')}) LIKE ?")
        params.append(f"%{w.lower()}%")
    if not conds:
        return []
    sql = (f"SELECT {_select(s, STOCK_FIELDS)} FROM {_ident(_physical(s['table']))} "
           f"WHERE {' OR '.join(conds)} ORDER BY {_col(s, 'item_id')}")
    return _cite(s, "item_id", query(sql, params))


def shipped_totals(sources: dict, item_ids: list[str]) -> dict[str, float]:
    """Totals summed in Python. Spark would push CAST(... AS DOUBLE) down to Postgres, which has no DOUBLE type."""
    if not item_ids:
        return {}
    sh = sources["shipments"]
    rows = query(f"SELECT {_col(sh, 'item_id')} AS item_id, {_col(sh, 'quantity')} AS quantity "
                 f"FROM {_ident(_physical(sh['table']))} WHERE {_col(sh, 'item_id')} IN ({', '.join('?' * len(item_ids))})", item_ids)
    totals: dict[str, float] = {}
    for r in rows:
        try:
            totals[r["item_id"]] = totals.get(r["item_id"], 0.0) + float(r["quantity"] or 0)
        except (TypeError, ValueError):
            continue
    return totals


def get_shipments(sources: dict, item_ids: list[str]) -> list[dict]:
    if not item_ids:
        return []
    sh, ct = sources["shipments"], sources["contacts"]
    s_fields = [f for f in ["ship_id", "item_id", "agency_id", "quantity", "unit", "ship_date"] if f in sh["mapping"]]
    c_fields = [f for f in ["name", "contact_name", "contact_email", "contact_phone"] if f in ct["mapping"]]
    select = ", ".join([f"d.{_col(sh, f)} AS {f}" for f in s_fields] + [f"a.{_col(ct, f)} AS {'agency_name' if f == 'name' else f}" for f in c_fields])
    sql = (f"SELECT {select} FROM {_ident(_physical(sh['table']))} d "
           f"JOIN {_ident(_physical(ct['table']))} a ON d.{_col(sh, 'agency_id')} = a.{_col(ct, 'agency_id')} "
           f"WHERE d.{_col(sh, 'item_id')} IN ({', '.join('?' * len(item_ids))}) "
           f"ORDER BY d.{_col(sh, 'ship_date') if 'ship_date' in sh['mapping'] else _col(sh, 'ship_id')}")
    return _cite(sh, "ship_id", query(sql, item_ids))


def count_agencies(sources: dict) -> int:
    ct = sources["contacts"]
    where = f" WHERE coalesce(lower({_col(ct, 'active')}), 'true') = 'true'" if "active" in ct["mapping"] else ""
    return int(query(f"SELECT COUNT(*) AS n FROM {_ident(_physical(ct['table']))}{where}")[0]["n"])


# Read-only browsing for the Stock and Shipments pages

def _search_where(source: dict, fields: list[str], q: str) -> tuple[str, list]:
    cols = [f for f in fields if f in source["mapping"]]
    q = (q or "").strip().lower()
    if not q or not cols:
        return "", []
    return " WHERE " + " OR ".join(f"lower({_col(source, f)}) LIKE ?" for f in cols), [f"%{q}%"] * len(cols)


def search_stock(sources: dict, q: str = "", limit: int = 200) -> list[dict]:
    s = sources["stock"]
    where, params = _search_where(s, ["item_id", "brand", "product", "upc", "lot_code", "location"], q)
    sql = (f"SELECT {_select(s, STOCK_FIELDS)} FROM {_ident(_physical(s['table']))}{where} "
           f"ORDER BY {_col(s, 'item_id')} LIMIT {int(limit)}")
    rows = _cite(s, "item_id", query(sql, params))
    shipped = shipped_totals(sources, [r["item_id"] for r in rows])
    for r in rows:
        try:
            r["quantity_on_hand"] = float(r.get("quantity_received") or 0) - shipped.get(r["item_id"], 0.0)
        except (TypeError, ValueError):
            r["quantity_on_hand"] = None
    return rows


def search_shipments(sources: dict, q: str = "", limit: int = 200) -> list[dict]:
    sh, ct, s = sources["shipments"], sources["contacts"], sources["stock"]
    s_fields = [f for f in ["ship_id", "item_id", "agency_id", "quantity", "unit", "ship_date"] if f in sh["mapping"]]
    select = ", ".join([f"d.{_col(sh, f)} AS {f}" for f in s_fields]
                       + [f"a.{_col(ct, 'name')} AS agency_name"]
                       + [f"i.{_col(s, f)} AS {f}" for f in ["brand", "product", "lot_code"] if f in s["mapping"]])
    q = (q or "").strip().lower()
    where, params = "", []
    if q:
        cols = [f"a.{_col(ct, 'name')}", f"d.{_col(sh, 'item_id')}"] + [f"i.{_col(s, f)}" for f in ["brand", "product"] if f in s["mapping"]]
        where = " WHERE " + " OR ".join(f"lower({c}) LIKE ?" for c in cols)
        params = [f"%{q}%"] * len(cols)
    order = _col(sh, "ship_date") if "ship_date" in sh["mapping"] else _col(sh, "ship_id")
    sql = (f"SELECT {select} FROM {_ident(_physical(sh['table']))} d "
           f"JOIN {_ident(_physical(ct['table']))} a ON d.{_col(sh, 'agency_id')} = a.{_col(ct, 'agency_id')} "
           f"LEFT JOIN {_ident(_physical(s['table']))} i ON d.{_col(sh, 'item_id')} = i.{_col(s, 'item_id')}"
           f"{where} ORDER BY d.{order} DESC LIMIT {int(limit)}")
    return _cite(sh, "ship_id", query(sql, params))


def list_agencies(sources: dict) -> list[dict]:
    ct, sh = sources["contacts"], sources["shipments"]
    fields = [f for f in ["agency_id", "name", "contact_name", "contact_email", "contact_phone", "active"] if f in ct["mapping"]]
    rows = _cite(ct, "agency_id", query(f"SELECT {_select(ct, fields)} FROM {_ident(_physical(ct['table']))} ORDER BY {_col(ct, 'name')}"))
    has_date = "ship_date" in sh["mapping"]
    date_col = f", {_col(sh, 'ship_date')} AS ship_date" if has_date else ""
    counts: dict[str, int] = {}
    last: dict[str, str] = {}
    for r in query(f"SELECT {_col(sh, 'agency_id')} AS agency_id{date_col} FROM {_ident(_physical(sh['table']))}"):
        aid = r["agency_id"]
        counts[aid] = counts.get(aid, 0) + 1
        if has_date and r.get("ship_date") and r["ship_date"] > last.get(aid, ""):
            last[aid] = r["ship_date"]
    for r in rows:
        r["shipments"] = counts.get(r["agency_id"], 0)
        r["last_shipment"] = last.get(r["agency_id"])
    return rows
