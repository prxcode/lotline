"""Load data/ into three Postgres databases and print the statements that register them in Zetaris."""
import os
import csv
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
# Which system holds which table; Zetaris sees three separate sources
SYSTEMS = {
    "warehouse": ["inventory"],
    "distribution": ["distributions", "agencies"],
    "recalls": ["recalls", "recall_products"],
}
HOST = os.getenv("LOTLINE_DB_HOST", "localhost")
PORT = int(os.getenv("LOTLINE_DB_PORT", "5433"))
USER = "lotline"
PASSWORD = os.getenv("LOTLINE_DB_PASSWORD", "lotline-local-only")
# How the Zetaris containers reach this database; Docker Desktop provides host.docker.internal
ZETARIS_SEES_HOST = os.getenv("LOTLINE_DB_HOST_FOR_ZETARIS", "host.docker.internal")


def _connect(db: str):
    import psycopg
    return psycopg.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, dbname=db, autocommit=True)


def load():
    data_dir = ROOT / os.getenv("DATA_DIR", "data")
    with _connect("warehouse") as con:
        existing = {r[0] for r in con.execute("SELECT datname FROM pg_database")}
        for db in SYSTEMS:
            if db not in existing:
                con.execute(f'CREATE DATABASE "{db}"')
    for db, tables in SYSTEMS.items():
        with _connect(db) as con:
            for table in tables:
                with open(data_dir / f"{table}.csv", newline="", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    header = next(reader)
                    rows = [[v if v != "" else None for v in r] for r in reader]
                # Text columns, like the local route, so UPC leading zeros survive
                con.execute(f'DROP TABLE IF EXISTS "{table}"')
                con.execute(f'CREATE TABLE "{table}" (' + ", ".join(f'"{c}" TEXT' for c in header) + ")")
                with con.cursor() as cur:
                    cur.executemany(f'INSERT INTO "{table}" VALUES (' + ", ".join(["%s"] * len(header)) + ")", rows)
                print(f"{db}.{table}: {len(rows)} rows")


def zetaris_statements() -> str:
    out = ["-- Run one statement at a time in the Zetaris SQL Editor (http://localhost:3000)"]
    for db, tables in SYSTEMS.items():
        source = f"lotline_{db}"
        out.append(f'CREATE DATASOURCE {source} DESCRIBE BY "Food bank {db} system" OPTIONS (\n'
                   f'  jdbcdriver "org.postgresql.Driver",\n'
                   f'  jdbcurl "jdbc:postgresql://{ZETARIS_SEES_HOST}:{PORT}/{db}",\n'
                   f'  username "{USER}",\n  password "{PASSWORD}",\n  fetchsize "1000"\n);')
        out.append(f"REGISTER DATASOURCE TABLES FROM {source};")
    out.append("SELECT COUNT(*) FROM lotline_warehouse.inventory;   -- expect 180")
    tables = ",".join(f"{t}=lotline_{db}.{t}" for db, ts in SYSTEMS.items() for t in ts)
    out.append(f"\n-- Then in .env:\n-- DATA_BACKEND=zetaris\n-- ZETARIS_TABLES={tables}")
    return "\n\n".join(out)


if __name__ == "__main__":
    if "--sql-only" not in sys.argv:
        load()
    print()
    print(zetaris_statements())
