from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    api_key: str
    data_dir: Path
    analyzer_target: str = "backend.api.stub:analyze_video"
    catalog_dir: Path = Path("backend/data")
    frontend_dist: Path | None = None
    cors_origins: tuple[str, ...] = ()
    public_origin: str | None = None
    start_worker: bool = True
    max_upload_bytes: int = 100 * 1024 * 1024
    timeout_seconds: float = 270

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.getenv("API_KEY", "")
        if not api_key:
            raise RuntimeError("API_KEY must be set")
        analyzer_target = os.getenv("ANALYZER_TARGET", "")
        if not analyzer_target:
            raise RuntimeError("ANALYZER_TARGET must be set (use backend.api.stub:analyze_video for local API development)")
        return cls(
            api_key=api_key,
            data_dir=Path(os.getenv("DATA_DIR", "var")),
            analyzer_target=analyzer_target,
            catalog_dir=Path(os.getenv("CATALOG_DIR", "backend/data")),
            frontend_dist=Path(value) if (value := os.getenv("FRONTEND_DIST")) else None,
            cors_origins=tuple(origin.strip() for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip()),
            public_origin=os.getenv("PUBLIC_ORIGIN") or None,
        )
