import importlib
import logging
import multiprocessing
from pathlib import Path
import time

from .config import Settings
from .jobs import JobStore
from .result import format_result


logger = logging.getLogger("uvicorn.error")


def _analyze_child(target: str, video_path: str, connection) -> None:
    try:
        module_name, function_name = target.split(":", 1)
        analyzer = getattr(importlib.import_module(module_name), function_name)
        connection.send(("ok", analyzer(video_path)))
    except Exception as exc:
        connection.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


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
            result = format_result(payload, self.settings.catalog_dir)
            self.store.finish(job_id, result)
            logger.info("Job %s done in %.2fs", job_id, time.monotonic() - started)
        except Exception as exc:
            public_error = str(exc) if isinstance(exc, TimeoutError) else "Analysis failed"
            self.store.fail(job_id, public_error)
            logger.exception("Job %s failed", job_id)
        finally:
            parent.close()
            child.close()
            if process_started:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=2)
            Path(job["video_path"]).unlink(missing_ok=True)
        return True
