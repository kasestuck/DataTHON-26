from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.config import Settings
from tests.fixtures import MOV_BYTES


def test_built_frontend_uses_browser_session_without_exposing_api_key(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>Front build</body></html>", encoding="utf-8")
    settings = Settings(api_key="private-jury-key", data_dir=tmp_path / "state", frontend_dist=dist, start_worker=False)
    with TestClient(create_app(settings)) as browser:
        page = browser.get("/")
        session = browser.get("/ui-api/v1/session")
        assert page.status_code == 200
        assert "Front build" in page.text
        assert "private-jury-key" not in page.text + session.text
        assert "httponly" in session.headers["set-cookie"].lower()
        csrf = session.json()["csrf_token"]
        assert browser.post("/api/v1/analyze", files={"file": ("clip.mov", MOV_BYTES)}).status_code == 401
        assert browser.post("/ui-api/v1/analyze", files={"file": ("clip.mov", MOV_BYTES)}).status_code == 403
        upload = browser.post("/ui-api/v1/analyze", headers={"X-CSRF-Token": csrf}, files={"file": ("clip.mov", MOV_BYTES)})
        assert upload.status_code == 200
        job_id = upload.json()["job_id"]
        status = browser.get(f"/ui-api/v1/jobs/{job_id}", headers={"X-CSRF-Token": csrf})
        assert status.json() == {"job_id": job_id, "status": "processing"}


def test_frontend_mount_is_optional_and_cors_is_allowlisted(tmp_path):
    settings = Settings(api_key="secret", data_dir=tmp_path, cors_origins=("https://trusted.example",), start_worker=False)
    with TestClient(create_app(settings)) as api:
        assert api.get("/").status_code == 404
        assert api.get("/ui-api/v1/session").status_code == 404
        allowed = api.options("/api/v1/analyze", headers={"Origin": "https://trusted.example", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "X-API-Key"})
        denied = api.options("/api/v1/analyze", headers={"Origin": "https://other.example", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "X-API-Key"})
    assert allowed.headers.get("access-control-allow-origin") == "https://trusted.example"
    assert denied.headers.get("access-control-allow-origin") is None
