"""Stage timings without exporting video frames. Full mode never uses mocks."""
import argparse
import json
from pathlib import Path
from statistics import mean
import time

from backend.analyzer.errors import AnalyzerError
from backend.analyzer.pipeline import analyze_video_report
from backend.analyzer.video import VideoReader


def benchmark_video(path, sample_fps=2., full=False):
    started = time.monotonic()
    with VideoReader(path) as reader:
        count = sum(1 for _ in reader.sample(sample_fps))
        duration = reader.info.duration_sec
    extraction = time.monotonic() - started
    result = {"video": Path(path).name, "duration_sec": duration, "sampled_frames": count,
        "extraction_sec": extraction, "extraction_under_10_sec": extraction < 10,
        "full_analysis": None, "peak_vram_bytes": None,
        "viewpoint_robustness": "unmeasured; requires separately annotated alternate camera views",
        "oom_behavior": "not exercised on real models"}
    if full:
        try:
            report = analyze_video_report(str(path))
            if report.is_mock:
                raise ValueError("Mock benchmarks cannot represent real model speed")
            result["full_analysis"] = report.timings
            result["diagnostic_only"] = report.diagnostic_only
            result["candidate_windows"] = report.candidate_windows
            result["peak_vram_bytes"] = report.peak_vram_bytes
            result["under_5_minutes"] = report.timings["wall_sec"] < 300
        except (AnalyzerError, OSError, ValueError) as exc:
            result["inference_error"] = type(exc).__name__
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--videos-dir", default="dataset/videos")
    parser.add_argument("--video", help="Benchmark one video instead of the directory")
    parser.add_argument("--fps", type=float, default=2.)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--output", default="backend/data/reports/benchmark.json")
    args = parser.parse_args()
    paths = [Path(args.video)] if args.video else sorted(p for p in Path(args.videos_dir).iterdir() if p.suffix.lower() == ".mov")
    results = []
    for path in paths:
        try:
            results.append(benchmark_video(path, args.fps, args.full))
        except (AnalyzerError, ValueError) as exc:
            results.append({"video": path.name, "error": type(exc).__name__})
    times = [row["extraction_sec"] for row in results if "extraction_sec" in row]
    report = {"mode": "real_inference" if args.full else "extraction_only", "videos": results,
        "summary": {"videos": len(results), "readable": len(times),
            "mean_extraction_sec": mean(times) if times else None,
            "max_extraction_sec": max(times) if times else None}}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
