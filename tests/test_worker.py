import time
from unittest.mock import patch
from pathlib import Path
import sqlite3

import pytest

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.config import Settings
from backend.api.jobs import JobStore
from backend.api.worker import JobWorker
from tests.fixtures import MOV_BYTES


def test_worker_completes_queued_jobs_one_by_one(tmp_path):
    settings = Settings(api_key="secret", data_dir=tmp_path, analyzer_target="backend.api.stub:analyze_video", start_worker=False)
    with TestClient(create_app(settings)) as api:
        ids = [api.post("/api/v1/analyze", headers={"X-API-Key": "secret"}, files={"file": (f"{i}.mov", MOV_BYTES)}).json()["job_id"] for i in range(2)]
        worker = JobWorker(JobStore(tmp_path / "jobs.sqlite3"), settings)
        assert worker.process_next() is True
        first = api.get(f"/api/v1/jobs/{ids[0]}", headers={"X-API-Key": "secret"}).json()
        second = api.get(f"/api/v1/jobs/{ids[1]}", headers={"X-API-Key": "secret"}).json()
        assert first == {"job_id": ids[0], "status": "done", "verdict": "no_violation", "violations": []}
        assert second == {"job_id": ids[1], "status": "processing"}
        assert worker.process_next() is True
        assert api.get(f"/api/v1/jobs/{ids[1]}", headers={"X-API-Key": "secret"}).json()["status"] == "done"
        assert not list((tmp_path / "uploads").glob("*.mov"))


def test_worker_marks_analyzer_failure_and_continues(tmp_path):
    settings = Settings(api_key="secret", data_dir=tmp_path, analyzer_target="backend.api.stub:raise_error", start_worker=False)
    store = JobStore(tmp_path / "jobs.sqlite3")
    first = store.create(tmp_path / "one.mov")
    second = store.create(tmp_path / "two.mov")
    worker = JobWorker(store, settings)
    assert worker.process_next() is True
    assert store.get(first)["status"] == "failed"
    assert store.get(first)["error"] == "Analysis failed"
    assert worker.process_next() is True
    assert store.get(second)["status"] == "failed"


def test_worker_logs_processing_stages(tmp_path, caplog):
    settings = Settings(api_key="secret", data_dir=tmp_path, analyzer_target="backend.api.stub:analyze_video", start_worker=False)
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create(tmp_path / "one.mov")
    with caplog.at_level("INFO", logger="uvicorn.error"):
        assert JobWorker(store, settings).process_next() is True
    assert f"Job {job_id} claimed" in caplog.text
    assert f"Job {job_id} analyzer started" in caplog.text
    assert f"Job {job_id} done" in caplog.text


def test_worker_times_out_hung_analyzer(tmp_path):
    settings = Settings(api_key="secret", data_dir=tmp_path, analyzer_target="backend.api.stub:hang", timeout_seconds=0.2, start_worker=False)
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create(tmp_path / "one.mov")
    started = time.monotonic()
    JobWorker(store, settings).process_next()
    assert time.monotonic() - started < 3
    assert store.get(job_id)["status"] == "failed"
    assert "timeout" in store.get(job_id)["error"].lower()


def test_store_recovers_unfinished_job_after_restart(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create(tmp_path / "one.mov")
    assert store.claim_next()["job_id"] == job_id
    reopened = JobStore(tmp_path / "jobs.sqlite3")
    reopened.recover_unfinished()
    assert reopened.claim_next()["job_id"] == job_id


def test_worker_marks_spawn_failure_without_crashing(tmp_path):
    settings = Settings(api_key="secret", data_dir=tmp_path, start_worker=False)
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create(tmp_path / "one.mov")

    class FailedProcess:
        def start(self):
            raise OSError("cannot start child")

        def is_alive(self):
            return False

        def join(self, timeout=None):
            raise AssertionError("unstarted child must not be joined")

    with patch("backend.api.worker.multiprocessing.get_context") as context:
        context.return_value.Pipe.return_value = (type("Connection", (), {"close": lambda self: None})(), type("Connection", (), {"close": lambda self: None})())
        context.return_value.Process.return_value = FailedProcess()
        assert JobWorker(store, settings).process_next() is True
    assert store.get(job_id)["status"] == "failed"


def test_worker_serializes_a_synthetic_finding_without_rule(tmp_path):
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    (catalog / "violation_types.json").write_text('{"counter_event": {"name": "Событие у кассы", "description": "Второй человек"}}', encoding="utf-8")
    (catalog / "rules.json").write_text('{"counter_event": {"status": "needs_review"}}', encoding="utf-8")
    settings = Settings(api_key="secret", data_dir=tmp_path, catalog_dir=catalog, analyzer_target="tests.fake_analyzer:one_finding", start_worker=False)
    with TestClient(create_app(settings)) as api:
        job_id = api.post("/api/v1/analyze", headers={"X-API-Key": "secret"}, files={"file": ("sample.mov", MOV_BYTES)}).json()["job_id"]
        assert JobWorker(JobStore(tmp_path / "jobs.sqlite3"), settings).process_next() is True
        result = api.get(f"/api/v1/jobs/{job_id}", headers={"X-API-Key": "secret"}).json()
    assert result["status"] == "done"
    assert result["verdict"] == "violation"
    assert result["violations"] == [{
        "type": "Событие у кассы",
        "timecode_sec": 12.5,
        "description": "В кадре появился второй человек.",
        "evidence_frames": [{"timestamp_sec": 12.5, "image_base64": "/9j/2Q=="}],
    }]


def test_api_worker_allows_analyzer_to_spawn_its_own_child(tmp_path):
    settings = Settings(api_key="secret", data_dir=tmp_path, analyzer_target="tests.fake_analyzer:nested_spawn", start_worker=False)
    with TestClient(create_app(settings)) as api:
        job_id = api.post("/api/v1/analyze", headers={"X-API-Key": "secret"}, files={"file": ("sample.mov", MOV_BYTES)}).json()["job_id"]
        assert JobWorker(JobStore(tmp_path / "jobs.sqlite3"), settings).process_next() is True
        result = api.get(f"/api/v1/jobs/{job_id}", headers={"X-API-Key": "secret"}).json()
    assert result == {"job_id": job_id, "status": "done", "verdict": "no_violation", "violations": []}


def test_timeout_stops_nested_analyzer_process(tmp_path):
    settings = Settings(api_key="secret", data_dir=tmp_path, analyzer_target="tests.fake_analyzer:nested_hang",
                        timeout_seconds=4, start_worker=False)
    store = JobStore(tmp_path / 'jobs.sqlite3')
    video = tmp_path / 'nested.mov'
    job_id = store.create(video)
    JobWorker(store, settings).process_next()
    assert Path(str(video) + '.started').exists(), 'Nested child must actually start for this regression test'
    assert store.get(job_id)['status'] == 'failed'
    assert 'timeout' in store.get(job_id)['error'].lower()
    time.sleep(3)
    assert not Path(str(video) + '.survived').exists(), 'Nested worker escaped timeout cleanup'


def test_store_closes_sqlite_handles_after_each_operation(tmp_path, monkeypatch):
    connect = sqlite3.connect
    connections = []

    def tracked_connect(*args, **kwargs):
        db = connect(*args, **kwargs)
        connections.append(db)
        return db

    monkeypatch.setattr('backend.api.jobs.sqlite3.connect', tracked_connect)
    store = JobStore(tmp_path / 'jobs.sqlite3')
    job_id = store.create(tmp_path / 'sample.mov')
    store.claim_next()
    store.finish(job_id, {'verdict': 'no_violation', 'violations': []})
    assert store.get(job_id)['status'] == 'done'
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            db.execute('SELECT 1')
