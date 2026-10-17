"""Bounded cleanup of Back 2's own upload files and finished jobs."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

from .jobs import JobStore


_UPLOAD_NAME = re.compile(r"[0-9a-f]{32}\.mov")


def cleanup_stale(
    store: JobStore,
    uploads_dir: Path,
    *,
    now: datetime | None = None,
    retention_hours: int = 24,
) -> int:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=retention_hours)
    active = store.active_video_paths()
    removed = 0
    for path in uploads_dir.glob("*.mov"):
        if not _UPLOAD_NAME.fullmatch(path.name) or path.resolve() in active:
            continue
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) < cutoff:
                path.unlink()
                removed += 1
        except FileNotFoundError:
            continue
    store.prune_finished(cutoff)
    return removed
