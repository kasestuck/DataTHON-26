from fastapi.testclient import TestClient
from pathlib import Path
import pytest

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


@pytest.fixture
def site(tmp_path):
    settings = Settings(api_key="synthetic-server-only-key", data_dir=tmp_path,
                        frontend_dist=Path("frontend"), start_worker=False)
    with TestClient(create_app(settings)) as client:
        session = client.get("/ui-api/v1/session")
        client.headers["X-CSRF-Token"] = session.json()["csrf_token"]
        yield client


def test_real_frontend_assets_and_private_paths(site):
    for path in ("/", "/analyze.html", "/about.html", "/profile.html", "/app.js", "/api.js", "/config.js", "/style.css"):
        response = site.get(path)
        assert response.status_code == 200
        assert "synthetic-server-only-key" not in response.text
    for path in ("/.git/config", "/.env", "/backend/data/labels.csv", "/models/config.json", "/dataset/private.mov", "/start.ps1"):
        assert site.get(path).status_code == 404
    assert site.post("/api/v1/analyze", files={"file": ("x.mov", MOV_BYTES)}).status_code == 401


def test_browser_upload_29_mib_and_header_only_auth_rejected(site):
    # Upload transport/limit test only; no claim about decoding or AI inference.
    response = site.post("/ui-api/v1/analyze", files={"file": ("sample.MOV", MOV_BYTES + bytes(29 * 1024 * 1024))})
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    assert site.get(f"/ui-api/v1/jobs/{job_id}").json()["status"] == "processing"
    site.cookies.clear()
    assert site.get(f"/ui-api/v1/jobs/{job_id}").status_code == 403


@pytest.mark.parametrize("filename,content", [("x.mp4", MOV_BYTES), ("empty.mov", b""), ("bad.mov", b"broken")])
def test_browser_rejects_invalid_uploads(site, filename, content):
    response = site.post("/ui-api/v1/analyze", files={"file": (filename, content)})
    assert response.status_code == 400
    assert "job_id" not in response.json()


def test_browser_sessions_survive_second_tab_and_reject_cross_origin(site):
    csrf = site.headers["X-CSRF-Token"]
    assert site.get("/ui-api/v1/session").json()["csrf_token"] == csrf
    for headers in ({"Origin": "https://foreign.example"}, {"Sec-Fetch-Site": "cross-site"}):
        assert site.get("/ui-api/v1/session", headers=headers).status_code == 403
        assert site.post("/ui-api/v1/analyze", headers=headers, files={"file": ("x.mov", MOV_BYTES)}).status_code == 403
