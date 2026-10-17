from datetime import datetime, timezone
import os
import sqlite3

from backend.api.cleanup import cleanup_stale
from backend.api.jobs import JobStore


def test_cleanup_removes_old_orphan_but_keeps_active_and_unrelated_files(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    active = uploads / ("a" * 32 + ".mov")
    orphan = uploads / ("b" * 32 + ".mov")
    unrelated = uploads / "user-file.mov"
    for path in (active, orphan, unrelated):
        path.write_bytes(b"data")
        os.utime(path, (1, 1))
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.create(active)

    removed = cleanup_stale(store, uploads, now=datetime(2026, 10, 17, tzinfo=timezone.utc), retention_hours=24)

    assert removed == 1
    assert active.exists()
    assert unrelated.exists()
    assert not orphan.exists()


def test_cleanup_prunes_old_finished_jobs_but_keeps_processing_jobs(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    store = JobStore(tmp_path / "jobs.sqlite3")
    finished_id = store.create(uploads / "finished.mov")
    active_id = store.create(uploads / "active.mov")
    store.finish(finished_id, {"verdict": "no_violation", "violations": []})
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE jobs SET created_at = '2020-01-01 00:00:00'")

    cleanup_stale(store, uploads, now=datetime(2026, 10, 17, tzinfo=timezone.utc), retention_hours=24)

    assert store.get(finished_id) is None
    assert store.get(active_id)["status"] == "processing"
