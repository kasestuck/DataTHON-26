import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import time
import subprocess
from collections import Counter
from statistics import mean, median

from backend.analyzer.errors import AnalyzerError
from backend.analyzer.catalog import load_catalog
from backend.analyzer.config import AnalyzerConfig
from backend.analyzer.labels import read_labels
from backend.analyzer.labels import parse_verdict
from backend.analyzer.metrics import compute_metrics
from backend.analyzer.pipeline import analyze_video_report
from backend.analyzer.policy import load_policy


def verify_findings(findings, valid_types=None):
    """Audit the actual integration boundary, including decodable JPEG evidence."""
    import cv2
    import numpy as np

    output = []
    for finding in findings:
        if valid_types is not None and finding.violation_type not in valid_types:
            raise ValueError("Finding type outside the configured catalog")
        if not 1 <= len(finding.evidence_frames) <= 3:
            raise ValueError("Finding must have 1–3 evidence frames")
        evidence = []
        for frame in finding.evidence_frames:
            data = frame.jpeg_bytes
            if (not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9")
                    or cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR) is None):
                raise ValueError("Finding evidence is not a decodable JPEG")
            evidence.append({"timestamp_sec": frame.timestamp_sec, "jpeg_bytes": len(data),
                             "sha256": hashlib.sha256(data).hexdigest()})
        output.append({"violation_type": finding.violation_type, "timecode_sec": finding.timecode_sec,
                       "confidence": finding.confidence, "evidence": evidence})
    return output


def _save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def evaluate(labels, videos_dir, runner=analyze_video_report, tolerance_sec=3., *, catalog_available=True,
             valid_types=None, checkpoint=None, fingerprint=None, resume=False, progress=None):
    if not math.isfinite(tolerance_sec) or tolerance_sec < 0:
        raise ValueError("Localization tolerance must be finite and non-negative")
    predictions, times, skipped, details = {}, {}, [], {}
    if resume and checkpoint and Path(checkpoint).exists():
        cached = json.loads(Path(checkpoint).read_text(encoding="utf-8"))
        if not fingerprint or cached.get("fingerprint") != fingerprint:
            raise ValueError("Evaluation checkpoint does not match data/catalog/configuration")
        predictions, times, skipped = cached["predictions"], cached["timings"], cached["skipped"]
        details = cached.get("finding_details", {})
    videos = list(dict.fromkeys(row["video"] for row in labels))
    completed = set(predictions) | {item["video"] for item in skipped}
    for position, video in enumerate(videos, 1):
        if Path(video).name != video or "\\" in video:
            raise ValueError("Label filenames must not contain directory traversal")
        if video in completed:
            continue
        if not catalog_available:
            skipped.append({"video": video, "reason": "empty_catalog_diagnostics_only"})
            continue
        path = Path(videos_dir) / video
        started = time.monotonic()
        try:
            result = runner(str(path))
            if result.is_mock:
                skipped.append({"video": video, "reason": "mock_forbidden"})
                continue
            if result.diagnostic_only:
                skipped.append({"video": video, "reason": "empty_catalog_diagnostics_only"})
                continue
            predictions[video] = verify_findings(result.findings, valid_types)
            details[video] = [{"violation_type": f.violation_type, "timecode_sec": f.timecode_sec,
                "description": f.description, "confidence": f.confidence,
                "evidence": predictions[video][i]["evidence"]} for i, f in enumerate(result.findings)]
            times[video] = {**result.timings, "wall_sec": time.monotonic() - started,
                "peak_vram_bytes": result.peak_vram_bytes, "sampled_frames": result.sampled_frames,
                "candidate_windows": result.candidate_windows, "diagnostic_only": result.diagnostic_only,
                "diagnostic_reasons": dict(Counter(result.diagnostics))}
            try:
                gpu = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=3, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                times[video]["gpu_memory_after_worker_mb"] = int(gpu.stdout.strip().splitlines()[0]) if gpu.returncode == 0 else None
            except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
                times[video]["gpu_memory_after_worker_mb"] = None
        except (AnalyzerError, OSError, ValueError) as exc:
            skipped.append({"video": video, "reason": type(exc).__name__, "message": str(exc),
                            "wall_sec": time.monotonic() - started})
        if checkpoint:
            _save(checkpoint, {"fingerprint": fingerprint, "predictions": predictions, "timings": times,
                               "skipped": skipped, "finding_details": details})
        if progress:
            progress({"completed": position, "total": len(videos), "video": video,
                "status": "evaluated" if video in predictions else "failed",
                "findings": len(predictions[video]) if video in predictions else None,
                "expected_verdict": any(parse_verdict(r["has_violation"]) for r in labels if r["video"] == video),
                "expected_timecodes": [r["timecode_sec"] for r in labels if r["video"] == video and parse_verdict(r["has_violation"])],
                "predicted_verdict": bool(predictions[video]) if video in predictions else None,
                "predicted_events": [{"type": r["violation_type"], "timecode_sec": r["timecode_sec"]}
                    for r in predictions.get(video, [])],
                "error": next((r["reason"] for r in skipped if r["video"] == video), None),
                "wall_sec": round(time.monotonic() - started, 2)})
    report = compute_metrics(labels, predictions, tolerance_sec)
    report.update(status="complete" if len(predictions) == len(set(r["video"] for r in labels)) else "partial" if predictions else "unavailable",
                  skipped=skipped, timings=times, predictions=predictions, finding_details=details)
    walls = [row["wall_sec"] for row in times.values()]
    peaks = [row["peak_vram_bytes"] for row in times.values() if row.get("peak_vram_bytes") is not None]
    report["runtime"] = {"evaluated": len(walls), "mean_sec": mean(walls) if walls else None,
        "median_sec": median(walls) if walls else None, "max_sec": max(walls) if walls else None,
        "total_success_sec": sum(walls), "within_270_sec": sum(value < 270 for value in walls),
        "peak_vram_bytes": max(peaks) if peaks else None}
    report["annotation_review"] = [{"video": row["video"], "timecode_sec": row["timecode_sec"],
        "reason": row.get("notes", ""), "source_row": row.get("source_row", "")}
        for row in labels if row.get("review_status") == "needs_review"]
    report["manual_review"].extend({"video": item["video"], "reasons": [item["reason"]]} for item in skipped)
    return report


def main():
    parser = argparse.ArgumentParser(description="Evaluate real local inference, never mock metrics")
    parser.add_argument("--labels", default="backend/data/labels.csv")
    parser.add_argument("--videos-dir", default="dataset/videos")
    parser.add_argument("--tolerance-sec", type=float, default=3.)
    parser.add_argument("--output", default="backend/data/reports/evaluation.json")
    parser.add_argument("--resume", action="store_true", help="Resume only an identical data/model/config snapshot")
    args = parser.parse_args()
    if not math.isfinite(args.tolerance_sec) or args.tolerance_sec < 0:
        parser.error("tolerance must be non-negative")
    config = AnalyzerConfig.from_env()
    catalog = load_catalog(config.catalog_path)
    policy = load_policy(catalog, config.catalog_path, config.policy_path)
    labels = read_labels(args.labels)
    inputs = {"labels_sha256": hashlib.sha256(Path(args.labels).read_bytes()).hexdigest(),
        "catalog": catalog, "policy": policy, "config": asdict(config), "tolerance_sec": args.tolerance_sec,
        "code_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path("backend/analyzer").glob("*.py"))} | {
            "backend/scripts/evaluate.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        "videos": {video: {"size": (Path(args.videos_dir) / video).stat().st_size,
            "mtime_ns": (Path(args.videos_dir) / video).stat().st_mtime_ns}
            for video in dict.fromkeys(row["video"] for row in labels) if (Path(args.videos_dir) / video).is_file()},
        "models": {str(directory): json.loads((Path(directory) / "provenance.json").read_text(encoding="utf-8"))
            for directory in (config.detector_dir, config.vlm_dir) if directory and (Path(directory) / "provenance.json").is_file()}}
    fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    report = evaluate(labels, args.videos_dir, runner=lambda path: analyze_video_report(path, config),
        tolerance_sec=args.tolerance_sec, catalog_available=bool(catalog), valid_types=catalog,
        checkpoint=str(args.output) + ".checkpoint.json", fingerprint=fingerprint, resume=args.resume,
        progress=lambda value: print(json.dumps(value, ensure_ascii=True), flush=True))
    report["run_inputs"] = inputs
    report["fingerprint"] = fingerprint
    output = Path(args.output)
    _save(output, report)
    print(json.dumps({"status": report["status"], "evaluated_videos": report["evaluated_videos"],
        "skipped_videos": len(report["skipped"]), "report": str(output)}))


if __name__ == "__main__":
    main()
