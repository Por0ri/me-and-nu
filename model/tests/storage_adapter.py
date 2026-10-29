"""In-memory SQLite double for the batch's SQL adapter, never an application DB."""

import json
import sqlite3
from contextlib import contextmanager


class MemoryRecommendationStore:
    def __init__(self, content_ids=()):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE content(content_id INTEGER PRIMARY KEY);
            CREATE TABLE recommendation_round(
                recommendation_round_id INTEGER PRIMARY KEY AUTOINCREMENT,
                tap_id INTEGER NOT NULL, service_date TEXT NOT NULL, algorithm_version TEXT NOT NULL,
                UNIQUE(tap_id, service_date, algorithm_version));
            CREATE TABLE recommendation_item(
                recommendation_round_id INTEGER NOT NULL REFERENCES recommendation_round ON DELETE CASCADE,
                content_id INTEGER NOT NULL REFERENCES content,
                rank INTEGER NOT NULL CHECK(rank >= 1), score REAL, recommend_reason TEXT,
                UNIQUE(recommendation_round_id, rank), UNIQUE(recommendation_round_id, content_id));
        """)
        self.db.executemany("INSERT INTO content VALUES (?)", [(i,) for i in content_ids])
        self.lock_requests = 0

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    @contextmanager
    def cursor(self):
        yield self

    @staticmethod
    def parameters(values):
        return tuple(json.dumps(v.obj, ensure_ascii=False) if hasattr(v, "obj")
                     else v.isoformat() if hasattr(v, "isoformat") else v for v in values)

    def execute(self, statement, values=()):
        if statement.startswith("SELECT pg_advisory_xact_lock"):
            # PostgreSQL locking itself is deliberately not simulated or verified.
            self.lock_requests += 1
            self.current = None
            return
        self.current = self.db.execute(statement.replace("%s", "?"), self.parameters(values))

    def fetchone(self):
        return self.current.fetchone()

    def executemany(self, statement, values):
        self.db.executemany(statement.replace("%s", "?"), [self.parameters(row) for row in values])

    def snapshot(self):
        return {name: self.db.execute(f"SELECT * FROM {name} ORDER BY 1, 2").fetchall()
                for name in ("recommendation_round", "recommendation_item")}

    def close(self):
        self.db.close()


def self_test():
    from model.scripts.daily_recommend import write
    from model.tests.fixtures import synthetic_ranking
    posts, results, today = synthetic_ranking()
    store = MemoryRecommendationStore(p["id"] for p in posts)
    try:
        created = write(store, results, posts, today, False)
        repeated = write(store, results, posts, today, False)
        before = store.snapshot()
        bad_posts = [dict(p, id=99999) for p in posts]
        try:
            write(store, results, bad_posts, today, True)
        except sqlite3.IntegrityError:
            rolled_back = store.snapshot() == before
        else:
            rolled_back = False
        replaced = write(store, results, posts, today, True)
        count = len(store.snapshot()["recommendation_item"])
        assert (created, repeated, replaced, count, rolled_back) == (1, 0, 1, 4, True)
        return {"mode": "synthetic_offline", "created_rounds": created, "repeat_created": repeated,
                "replaced_rounds": replaced, "item_count": count, "replace_rollback_verified": rolled_back,
                "external_database_connections": 0,
                "limits": "Synthetic ranking and SQLite SQL-adapter check only; PostgreSQL locking, real data/model quality and deployment not verified."}
    finally:
        store.close()
