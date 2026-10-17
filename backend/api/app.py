from contextlib import asynccontextmanager
import hashlib
import hmac
import logging
from pathlib import Path
import secrets
from threading import Event, Thread
import time
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .cleanup import cleanup_stale
from .jobs import JobStore
from .worker import JobWorker


logger = logging.getLogger("uvicorn.error")


def create_app(settings: Settings) -> FastAPI:
    uploads = settings.data_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    jobs = JobStore(settings.data_dir / "jobs.sqlite3")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stop = Event()
        thread = None
        if settings.start_worker:
            jobs.recover_unfinished()
            removed = cleanup_stale(jobs, uploads)
            logger.info("Worker started; removed %d stale uploads", removed)
            worker = JobWorker(jobs, settings)

            def run_worker():
                next_cleanup = time.monotonic() + 3600
                while not stop.is_set():
                    if time.monotonic() >= next_cleanup:
                        removed = cleanup_stale(jobs, uploads)
                        logger.info("Periodic cleanup removed %d stale uploads", removed)
                        next_cleanup = time.monotonic() + 3600
                    if not worker.process_next():
                        stop.wait(0.2)

            thread = Thread(target=run_worker, daemon=True)
            thread.start()
        try:
            yield
        finally:
            stop.set()
            if thread is not None:
                thread.join(timeout=2)

    app = FastAPI(lifespan=lifespan)
    if settings.cors_origins:
        if "*" in settings.cors_origins:
            raise ValueError("CORS_ORIGINS must list explicit origins")
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["X-API-Key", "Content-Type", "X-CSRF-Token"],
            allow_credentials=False,
        )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    def require_key(x_api_key: str | None = Header(default=None)) -> None:
        if x_api_key is None or not secrets.compare_digest(x_api_key, settings.api_key):
            raise HTTPException(status_code=401, detail="Доступ запрещён: проверьте ключ доступа.")

    async def save_upload(file: UploadFile):
        if Path(file.filename or "").suffix.lower() != ".mov":
            raise HTTPException(status_code=400, detail="Выберите видеофайл в формате MOV.")
        path = uploads / f"{uuid4().hex}.mov"
        size = 0
        try:
            with path.open("wb") as target:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(status_code=413, detail="Размер файла превышает допустимый.")
                    target.write(chunk)
            if size == 0:
                raise HTTPException(status_code=400, detail="Выбранный файл пуст. Выберите другую видеозапись.")
            with path.open("rb") as uploaded:
                header = uploaded.read(12)
            atom_size = int.from_bytes(header[:4], "big") if len(header) >= 4 else 0
            if (
                len(header) < 12
                or header[4:8] not in {b"ftyp", b"moov", b"wide", b"free", b"mdat"}
                or atom_size < 12
                or atom_size > size
            ):
                raise HTTPException(status_code=400, detail="Не удалось прочитать видеофайл. Выберите файл в формате MOV.")
            job_id = jobs.create(path)
            logger.info("Job %s queued: %d bytes", job_id, size)
            return {"job_id": job_id}
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    def job_status(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Результат анализа не найден.")
        result = {"job_id": job_id, "status": job["status"]}
        if "result" in job:
            result.update(job["result"])
        if "error" in job:
            result["error"] = job["error"]
        return result

    @app.post("/api/v1/analyze", dependencies=[Depends(require_key)])
    async def analyze(file: UploadFile = File(...)):
        return await save_upload(file)

    @app.get("/api/v1/jobs/{job_id}", dependencies=[Depends(require_key)])
    def get_job(job_id: str):
        return job_status(job_id)

    if settings.frontend_dist is not None:
        if not settings.frontend_dist.is_dir():
            raise ValueError(f"FRONTEND_DIST does not exist: {settings.frontend_dist}")

        def csrf_for(session_id: str) -> str:
            return hmac.new(settings.api_key.encode(), session_id.encode(), hashlib.sha256).hexdigest()

        def require_same_origin(request: Request) -> None:
            expected = (settings.public_origin or str(request.base_url)).rstrip("/")
            origin = request.headers.get("origin")
            if (origin is not None and origin != expected) or request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
                raise HTTPException(status_code=403, detail="Не удалось подтвердить доступ. Обновите страницу и повторите попытку.")

        def require_browser(request: Request, x_csrf_token: str | None = Header(default=None)) -> None:
            require_same_origin(request)
            session_id = request.cookies.get("ui_session")
            if not session_id or not x_csrf_token or not secrets.compare_digest(x_csrf_token, csrf_for(session_id)):
                raise HTTPException(status_code=403, detail="Не удалось подтвердить доступ. Обновите страницу и повторите попытку.")

        @app.get("/ui-api/v1/session")
        def browser_session(request: Request, response: Response):
            require_same_origin(request)
            session_id = request.cookies.get("ui_session", "")
            # Reuse a session across tabs; issuing a new cookie invalidates another tab's CSRF token.
            if len(session_id) != 43 or not all(c.isascii() and (c.isalnum() or c in "_-") for c in session_id):
                session_id = secrets.token_urlsafe(32)
            response.set_cookie(
                "ui_session",
                session_id,
                max_age=3600,
                httponly=True,
                secure=bool(settings.public_origin and settings.public_origin.startswith("https://")),
                samesite="strict",
            )
            return {"csrf_token": csrf_for(session_id)}

        @app.post("/ui-api/v1/analyze", dependencies=[Depends(require_browser)])
        async def browser_analyze(file: UploadFile = File(...)):
            return await save_upload(file)

        @app.get("/ui-api/v1/jobs/{job_id}", dependencies=[Depends(require_browser)])
        def browser_job(job_id: str):
            return job_status(job_id)

        app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="frontend")

    return app


def app_from_env() -> FastAPI:
    return create_app(Settings.from_env())
