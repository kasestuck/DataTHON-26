"""One controlled local resolution experiment. Never reads reference labels."""
import argparse
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

from backend.analyzer.config import AnalyzerConfig
from backend.analyzer.pipeline import analyze_video_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video")
    parser.add_argument("--max-side", type=int, default=848)
    parser.add_argument("--output", default="backend/data/reports/resolution_probe.json")
    args = parser.parse_args()
    config = replace(AnalyzerConfig.from_env(), max_side=args.max_side)
    result = analyze_video_report(args.video, config)
    if result.is_mock or result.diagnostic_only:
        raise ValueError("Resolution experiment requires real inference with an active catalog")
    report = {"video": Path(args.video).name, "max_side": args.max_side,
        "timings": result.timings, "peak_vram_bytes": result.peak_vram_bytes,
        "sampled_frames": result.sampled_frames, "candidate_windows": result.candidate_windows,
        "diagnostic_reasons": dict(Counter(result.diagnostics)),
        "findings": [{"violation_type": f.violation_type, "timecode_sec": f.timecode_sec,
            "confidence": f.confidence, "jpeg_frames": len(f.evidence_frames)} for f in result.findings]}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()
