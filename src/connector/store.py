from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.transaction() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY, session TEXT NOT NULL, idem TEXT NOT NULL,
                fingerprint TEXT NOT NULL, item TEXT NOT NULL, quantity INTEGER NOT NULL,
                amount INTEGER NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
                state TEXT NOT NULL, assessment TEXT NOT NULL, features TEXT NOT NULL,
                provider_order TEXT UNIQUE, payment_id TEXT, review_note TEXT,
                UNIQUE(session, idem)
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, order_id TEXT NOT NULL, kind TEXT NOT NULL,
                created REAL NOT NULL, detail TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS webhook_events (digest TEXT PRIMARY KEY);
            CREATE INDEX IF NOT EXISTS order_session_time ON orders(session, created);
            """)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=25)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


def unpack(row) -> dict:
    result = dict(row)
    for key in ("assessment", "features"):
        result[key] = json.loads(result[key])
    return result
