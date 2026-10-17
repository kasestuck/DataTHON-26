import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from threading import Lock
from uuid import uuid4


class JobStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = Lock()
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs ("
                "job_id TEXT PRIMARY KEY, status TEXT NOT NULL, video_path TEXT NOT NULL, "
                "result_json TEXT, error TEXT, claimed INTEGER NOT NULL DEFAULT 0, "
                "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, video_path: Path) -> str:
        job_id = uuid4().hex
        with self.lock, self._connect() as db:
            db.execute(
                "INSERT INTO jobs (job_id, status, video_path) VALUES (?, 'processing', ?)",
                (job_id, str(video_path)),
            )
        return job_id

    def recover_unfinished(self) -> None:
        with self.lock, self._connect() as db:
            db.execute("UPDATE jobs SET claimed = 0 WHERE status = 'processing'")

    def claim_next(self) -> dict | None:
        with self.lock, self._connect() as db:
            row = db.execute(
                "SELECT job_id, video_path FROM jobs WHERE status = 'processing' AND claimed = 0 "
                "ORDER BY created_at, rowid LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET claimed = 1 WHERE job_id = ?", (row[0],))
        return {"job_id": row[0], "video_path": row[1]}

    def finish(self, job_id: str, result: dict) -> None:
        with self.lock, self._connect() as db:
            db.execute(
                "UPDATE jobs SET status = 'done', result_json = ? WHERE job_id = ?",
                (json.dumps(result, ensure_ascii=False), job_id),
            )

    def fail(self, job_id: str, error: str) -> None:
        with self.lock, self._connect() as db:
            db.execute("UPDATE jobs SET status = 'failed', error = ? WHERE job_id = ?", (error, job_id))

    def active_video_paths(self) -> set[Path]:
        with self.lock, self._connect() as db:
            rows = db.execute("SELECT video_path FROM jobs WHERE status = 'processing'").fetchall()
        return {Path(row[0]).resolve() for row in rows}

    def prune_finished(self, before: datetime) -> None:
        cutoff = before.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with self.lock, self._connect() as db:
            db.execute("DELETE FROM jobs WHERE status IN ('done', 'failed') AND created_at < ?", (cutoff,))

    def get(self, job_id: str) -> dict | None:
        with self.lock, self._connect() as db:
            row = db.execute(
                "SELECT job_id, status, video_path, result_json, error FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        data = {"job_id": row[0], "status": row[1], "video_path": row[2]}
        if row[3] is not None:
            data["result"] = json.loads(row[3])
        if row[4] is not None:
            data["error"] = row[4]
        return data
