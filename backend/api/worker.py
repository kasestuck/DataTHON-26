import importlib
import logging
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import time

from .config import Settings
from .jobs import JobStore
from .result import format_result
from .display_text import ANALYSIS_ERROR
from backend.analyzer.ipc import send_result


logger = logging.getLogger("uvicorn.error")


def _analyze_child(target: str, video_path: str, connection) -> None:
    try:
        if os.name == "posix":
            os.setsid()
        module_name, function_name = target.split(":", 1)
        analyzer = getattr(importlib.import_module(module_name), function_name)
        message = ("ok", analyzer(video_path))
    except Exception as exc:
        message = ("error", f"{type(exc).__name__}: {exc}")
    try:
        send_result(connection, message)
    finally:
        connection.close()


def _stop_analyzer(process) -> None:
    """Stop our worker AND its nested Back 1 CUDA worker on timeout/error."""
    if process.is_alive():
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError, subprocess.SubprocessError):
                logger.exception("Could not stop nested analyzer process tree")
        else:
            try:
                if os.getpgid(process.pid) == process.pid:
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.terminate()
            except ProcessLookupError:
                pass
        if process.is_alive():
            process.terminate()
    process.join(timeout=2)
    if process.is_alive():
        process.kill()
        process.join(timeout=2)
    process.close()


class JobWorker:
    def __init__(self, store: JobStore, settings: Settings):
        self.store = store
        self.settings = settings

    def process_next(self) -> bool:
        job = self.store.claim_next()
        if job is None:
            return False
        job_id = job["job_id"]
        logger.info("Job %s claimed", job_id)
        started = time.monotonic()
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(
            target=_analyze_child,
            args=(self.settings.analyzer_target, job["video_path"], child),
        )
        process_started = False
        try:
            process.start()
            process_started = True
            logger.info("Job %s analyzer started", job_id)
            child.close()
            if not parent.poll(self.settings.timeout_seconds):
                raise TimeoutError(f"Analyzer timeout after {self.settings.timeout_seconds} seconds")
            status, payload = parent.recv()
            if status == "error":
                raise RuntimeError(payload)
            result = format_result(payload, self.settings.catalog_dir,
                diagnostics_path=self.settings.data_dir / "diagnostics" / "text" / f"{job_id}.json")
            self.store.finish(job_id, result)
            logger.info("Job %s done in %.2fs", job_id, time.monotonic() - started)
        except Exception:
            public_error = ANALYSIS_ERROR
            self.store.fail(job_id, public_error)
            logger.exception("Job %s failed", job_id)
        finally:
            parent.close()
            child.close()
            if process_started:
                _stop_analyzer(process)
            Path(job["video_path"]).unlink(missing_ok=True)
        return True
