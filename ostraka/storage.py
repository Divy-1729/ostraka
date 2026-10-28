import json
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS papers (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS annotations (id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, data TEXT NOT NULL);
            ''')
            for row in db.execute("SELECT id,data FROM jobs").fetchall():
                job = json.loads(row[1])
                if job["status"] in ("queued", "running"):
                    job.update(status="failed", message="The server restarted. Retry generation; completed guide sections were saved.")
                    db.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(job), row[0]))

    def connect(self):
        db = sqlite3.connect(self.root / "library.sqlite3", timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def save(self, table, item, paper_id=None):
        if table not in ("papers", "annotations", "jobs"):
            raise ValueError("Unknown table")
        with self.connect() as db:
            if table == "papers":
                db.execute("INSERT OR REPLACE INTO papers VALUES (?,?)", (item["id"], json.dumps(item)))
            else:
                db.execute(f"INSERT OR REPLACE INTO {table} VALUES (?,?,?)", (item["id"], paper_id, json.dumps(item)))

    def get(self, table, item_id):
        if table not in ("papers", "annotations", "jobs"):
            raise ValueError("Unknown table")
        with self.connect() as db:
            row = db.execute(f"SELECT data FROM {table} WHERE id=?", (item_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, table, paper_id=None):
        if table not in ("papers", "annotations", "jobs"):
            raise ValueError("Unknown table")
        query = f"SELECT data FROM {table}"
        args = ()
        if paper_id is not None:
            query += " WHERE paper_id=?"
            args = (paper_id,)
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(query, args).fetchall()]

    def delete_annotation(self, item_id, paper_id):
        with self.connect() as db:
            db.execute("DELETE FROM annotations WHERE id=? AND paper_id=?", (item_id, paper_id))

    def delete_paper(self, item_id):
        with self.connect() as db:
            for table in ("annotations", "jobs"):
                db.execute(f"DELETE FROM {table} WHERE paper_id=?", (item_id,))
            db.execute("DELETE FROM papers WHERE id=?", (item_id,))
        (self.root / f"{item_id}.pdf").unlink(missing_ok=True)
        shutil.rmtree(self.root / f"{item_id}-assets", ignore_errors=True)
