from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3


class LiveStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS live_assessments (
              id TEXT PRIMARY KEY, tx_ref TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
              customer TEXT NOT NULL, device TEXT, payee TEXT NOT NULL, amount INTEGER NOT NULL,
              created REAL NOT NULL, source TEXT NOT NULL, environment TEXT NOT NULL, result TEXT NOT NULL,
              permit_hash TEXT, permit_expires REAL, consumed INTEGER NOT NULL DEFAULT 0,
              consumed_at REAL, payment_status TEXT, outcome_ref TEXT, outcome_at REAL, features TEXT NOT NULL,
              reviewed INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS live_customer_time ON live_assessments(customer,created);
            CREATE TABLE IF NOT EXISTS live_nonces(nonce TEXT PRIMARY KEY,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS live_outcomes(event_ref TEXT PRIMARY KEY,fingerprint TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS live_labels(assessment_id TEXT PRIMARY KEY,label INTEGER NOT NULL,evidence TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS live_console_sessions(token_hash TEXT PRIMARY KEY,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS live_connection(id INTEGER PRIMARY KEY CHECK(id=1),last_seen REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS live_revision(id INTEGER PRIMARY KEY CHECK(id=1),value INTEGER NOT NULL);
            INSERT OR IGNORE INTO live_revision VALUES(1,0);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(live_assessments)")}
            if "outcome_at" not in columns:
                db.execute("ALTER TABLE live_assessments ADD COLUMN outcome_at REAL")
            if "reviewed" not in columns:
                db.execute("ALTER TABLE live_assessments ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10)
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
