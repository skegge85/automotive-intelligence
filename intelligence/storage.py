from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Store:
    def __init__(self, path):
        dsn = os.getenv("DATABASE_URL")
        self.postgres = bool(dsn)
        if self.postgres:
            import psycopg
            from psycopg.rows import dict_row
            self.connection = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10, autocommit=True)
        else:
            self.connection = sqlite3.connect(path, timeout=30)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))

    def execute(self, sql, values=()):
        return self.connection.execute(sql.replace("?", "%s") if self.postgres else sql, values)

    def seed(self, registry):
        with self.transaction():
            for row in registry["employers"]:
                self.execute("""insert into employers
                    (id,name,rank,ranking_year,priority,portal,method,board,metadata)
                    values (?,?,?,?,?,?,?,?,?) on conflict(id) do update set
                    name=excluded.name, rank=excluded.rank, ranking_year=excluded.ranking_year,
                    priority=excluded.priority, portal=excluded.portal, method=excluded.method,
                    board=excluded.board, metadata=excluded.metadata""",
                    (row["id"], row["name"], row["rank"], registry["ranking_year"] if row["rank"] else None,
                     int(row["priority"]), row["portal"], row["method"], row["board"], encode(row)))

    def transaction(self):
        return self.connection.transaction() if self.postgres else self.connection

    def close(self):
        self.connection.close()
