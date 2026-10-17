"""Real local model smoke test using only a generated geometric video."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import time

import cv2
import numpy as np

from backend.analyzer.config import AnalyzerConfig
from backend.analyzer.pipeline import analyze_video_report
from backend.analyzer.errors import InferenceError


def make_synthetic_video(path):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 192))
    if not writer.isOpened():
        raise RuntimeError("Synthetic fixture video codec unavailable")
    try:
        for index in range(60):
            image = np.full((192, 320, 3), 200, np.uint8)
            x = 20 + index * 3
            cv2.rectangle(image, (x, 70), (x + 35, 105), (0, 0, 255), -1)
            writer.write(image)
    finally:
        writer.release()


def smoke(config, runner=analyze_video_report, *, oom_check=False, catalog_check=False):
    root = Path(__file__).resolve().parents[1] / "analyzer" / "output"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="synthetic-smoke-", dir=root) as directory:
        path = Path(directory) / "synthetic.mov"
        catalog_path = Path(directory) / "empty_catalog.json"
        catalog_path.write_text('{}', encoding="utf-8")
        make_synthetic_video(path)
        try:
            report = runner(str(path), replace(config, catalog_path=config.catalog_path if catalog_check else str(catalog_path), sample_fps=1,
                max_side=384, window_frames=4, max_windows=1,
                memory_fraction=.005 if oom_check else config.memory_fraction))
        except InferenceError as exc:
            if oom_check and "memory" in str(exc).lower():
                return {"input": "synthetic_geometry_only", "success": True, "oom_handled": True,
                    "allocator_fraction": .005, "exception_type": type(exc).__name__}
            raise
        if oom_check:
            raise RuntimeError("Expected enforced allocator limit to reject model allocation")
        if report.is_mock:
            raise ValueError("Real model smoke test must not use a mock")
        return {"input": "synthetic_geometry_only", "success": True,
            "diagnostic_only": report.diagnostic_only,
            "timings": report.timings, "peak_vram_bytes": report.peak_vram_bytes,
            "sampled_frames": report.sampled_frames, "candidate_windows": report.candidate_windows,
            "findings": len(report.findings), "diagnostic_observations": len(report.diagnostics)}


def smoke_detector(config):
    from backend.analyzer.detector import LocalRTDetr
    import torch

    if config.device.startswith("cuda"):
        torch.cuda.reset_peak_memory_stats()
    started = time.monotonic()
    detector = LocalRTDetr(config.detector_dir, config.device)
    loaded = time.monotonic()
    detections = detector.detect(np.zeros((192, 320, 3), np.uint8))
    if config.device.startswith("cuda"):
        torch.cuda.synchronize()
    first_inference_sec = time.monotonic() - loaded
    warm_times = []
    for _ in range(5):
        tick = time.monotonic()
        detector.detect(np.zeros((192, 320, 3), np.uint8))
        if config.device.startswith("cuda"):
            torch.cuda.synchronize()
        warm_times.append(time.monotonic() - tick)
    return {"input": "synthetic_blank_image_only", "success": True,
        "model_load_sec": loaded - started, "detection_sec": first_inference_sec,
        "warm_detection_mean_sec": sum(warm_times) / len(warm_times), "warm_detection_max_sec": max(warm_times),
        "device": str(detector.model.device), "person_class_supported": "person" in detector.model.config.id2label.values(),
        "synthetic_detections": len(detections),
        "peak_vram_bytes": torch.cuda.max_memory_allocated() if config.device.startswith("cuda") else None}


def main():
    parser = argparse.ArgumentParser(description="Run real approved weights on synthetic frames, no private data")
    parser.add_argument("--output", default="backend/data/reports/smoke_models.json")
    parser.add_argument("--detector-only", action="store_true")
    parser.add_argument("--catalog-check", action="store_true", help="Use the real catalog on synthetic geometry")
    parser.add_argument("--oom-check", action="store_true", help="Validate error handling with a 0.5% allocator cap in the child process")
    args = parser.parse_args()
    result = smoke_detector(AnalyzerConfig.from_env()) if args.detector_only else smoke(
        AnalyzerConfig.from_env(), oom_check=args.oom_check, catalog_check=args.catalog_check)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
