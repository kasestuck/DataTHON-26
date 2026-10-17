from pathlib import Path
import time

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.config import Settings
from tests.fixtures import MOV_BYTES


def client(tmp_path: Path) -> TestClient:
    app = create_app(Settings(api_key="test-secret", data_dir=tmp_path, analyzer_target="backend.api.stub:analyze_video", start_worker=False))
    return TestClient(app)


def test_upload_requires_api_key(tmp_path):
    with client(tmp_path) as api:
        response = api.post("/api/v1/analyze", files={"file": ("clip.mov", MOV_BYTES)})
    assert response.status_code == 401


def test_upload_returns_job_id_and_status(tmp_path):
    with client(tmp_path) as api:
        response = api.post("/api/v1/analyze", headers={"X-API-Key": "test-secret"}, files={"file": ("clip.mov", MOV_BYTES)})
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        status = api.get(f"/api/v1/jobs/{job_id}", headers={"X-API-Key": "test-secret"})
    assert status.status_code == 200
    assert status.json() == {"job_id": job_id, "status": "processing"}
    assert len(list((tmp_path / "uploads").glob("*.mov"))) == 1


def test_upload_rejects_other_extensions(tmp_path):
    with client(tmp_path) as api:
        response = api.post("/api/v1/analyze", headers={"X-API-Key": "test-secret"}, files={"file": ("clip.mp4", MOV_BYTES)})
    assert response.status_code == 400
    assert not list((tmp_path / "uploads").glob("*"))


def test_get_unknown_job_returns_404(tmp_path):
    with client(tmp_path) as api:
        response = api.get("/api/v1/jobs/missing", headers={"X-API-Key": "test-secret"})
    assert response.status_code == 404


def test_get_job_requires_api_key(tmp_path):
    with client(tmp_path) as api:
        response = api.get("/api/v1/jobs/anything")
    assert response.status_code == 401


def test_upload_rejects_oversized_file_without_leaving_partial_upload(tmp_path):
    settings = Settings(api_key="test-secret", data_dir=tmp_path, max_upload_bytes=3, start_worker=False)
    with TestClient(create_app(settings)) as api:
        response = api.post("/api/v1/analyze", headers={"X-API-Key": "test-secret"}, files={"file": ("clip.mov", MOV_BYTES)})
    assert response.status_code == 413
    assert not list((tmp_path / "uploads").glob("*"))


def test_lifespan_worker_finishes_an_upload(tmp_path):
    settings = Settings(api_key="test-secret", data_dir=tmp_path, analyzer_target="backend.api.stub:analyze_video")
    with TestClient(create_app(settings)) as api:
        job_id = api.post("/api/v1/analyze", headers={"X-API-Key": "test-secret"}, files={"file": ("clip.mov", MOV_BYTES)}).json()["job_id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = api.get(f"/api/v1/jobs/{job_id}", headers={"X-API-Key": "test-secret"}).json()
            if result["status"] == "done":
                break
            time.sleep(0.1)
    assert result == {"job_id": job_id, "status": "done", "verdict": "no_violation", "violations": []}


def test_health_is_available_without_api_key(tmp_path):
    with client(tmp_path) as api:
        response = api.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_damaged_mov_is_rejected_before_queueing(tmp_path):
    with client(tmp_path) as api:
        response = api.post("/api/v1/analyze", headers={"X-API-Key": "test-secret"}, files={"file": ("damaged.mov", b"not a movie")})
    assert response.status_code == 400
    assert not list((tmp_path / "uploads").glob("*.mov"))


def test_upload_accepts_29_mib_mov(tmp_path):
    video = MOV_BYTES + bytes(29 * 1024 * 1024 - len(MOV_BYTES))
    with client(tmp_path) as api:
        response = api.post("/api/v1/analyze", headers={"X-API-Key": "test-secret"}, files={"file": ("clip.mov", video)})
    assert response.status_code == 200
    stored = next((tmp_path / "uploads").glob("*.mov"))
    assert stored.stat().st_size == len(video)
