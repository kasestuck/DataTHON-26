from dataclasses import dataclass
import math
import os
from pathlib import Path

DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "data" / "violation_types.json"
DEFAULT_MODELS = Path(__file__).resolve().parents[2] / "models"
DEFAULT_TEMP_DIR = Path(__file__).resolve().parent / "output"


@dataclass(frozen=True)
class AnalyzerConfig:
    catalog_path: str = str(DEFAULT_CATALOG)
    detector_dir: str | None = str(DEFAULT_MODELS / "rtdetr_r18vd")
    vlm_dir: str | None = str(DEFAULT_MODELS / "qwen3_vl_2b")
    device: str = "cuda"
    confidence_threshold: float = .75
    sample_fps: float = 2.
    max_side: int = 640
    window_frames: int = 6
    max_windows: int = 12
    max_sample_frames: int = 600
    timeout_sec: float = 260.
    vlm_timeout_sec: float = 35.
    context_sec: float = 10.
    policy_path: str | None = None
    dedup_sec: float = 4.
    memory_fraction: float | None = None  # Optional per-process PyTorch allocator cap.
    temp_dir: str = str(DEFAULT_TEMP_DIR)
    prompt_version: str = "baseline"
    record_window_audit: bool = False
    candidate_strategy: str = "uniform"
    include_event_anchor: bool = True
    release_cuda_cache: bool = True

    def __post_init__(self):
        numeric = [self.confidence_threshold, self.sample_fps, self.timeout_sec, self.vlm_timeout_sec, self.dedup_sec, self.context_sec]
        if not all(math.isfinite(x) for x in numeric):
            raise ValueError("Configuration values must be finite")
        if self.memory_fraction is not None and (not math.isfinite(self.memory_fraction) or not 0 < self.memory_fraction <= 1):
            raise ValueError("memory_fraction must be in (0, 1]")
        if not 0 <= self.confidence_threshold <= 1 or not 1 <= self.sample_fps <= 2:
            raise ValueError("Invalid threshold or sampling rate (1–2 FPS)")
        if not 0 < self.timeout_sec <= 260 or self.vlm_timeout_sec <= 0 or self.dedup_sec < 0 or not 0 <= self.context_sec <= 30:
            raise ValueError("Invalid deadline or deduplication window")
        if not 4 <= self.window_frames <= 8 or not 64 <= self.max_side <= 1280 or self.max_windows < 1 or self.max_sample_frames < 1:
            raise ValueError("Invalid memory/window budget")
        if self.prompt_version not in {"baseline", "sequence_v1", "frame_bound_v2"} or type(self.record_window_audit) is not bool:
            raise ValueError("Invalid prompt version or audit flag")
        if self.candidate_strategy not in {"uniform", "trigger"} or type(self.include_event_anchor) is not bool:
            raise ValueError("Invalid candidate strategy or event-anchor flag")
        if type(self.release_cuda_cache) is not bool:
            raise ValueError("Invalid CUDA cache policy")

    @classmethod
    def from_env(cls):
        return cls(catalog_path=os.getenv("ANALYZER_CATALOG", str(DEFAULT_CATALOG)),
            detector_dir=os.getenv("ANALYZER_DETECTOR_DIR", str(DEFAULT_MODELS / "rtdetr_r18vd")),
            vlm_dir=os.getenv("ANALYZER_VLM_DIR", str(DEFAULT_MODELS / "qwen3_vl_2b")),
            temp_dir=os.getenv("ANALYZER_TEMP_DIR", str(DEFAULT_TEMP_DIR)),
            policy_path=os.getenv("ANALYZER_POLICY"),
            confidence_threshold=float(os.getenv("ANALYZER_CONFIDENCE", ".75")))
