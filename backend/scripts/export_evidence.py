"""Reconstruct actual evaluated JPEG evidence and verify its original SHA-256.

No inference, training, or network access. Never fabricates a new finding.
"""
import argparse
import hashlib
import json
from pathlib import Path

from backend.analyzer.video import VideoReader


def export_evidence(report, videos_dir, output_dir, max_examples=5):
    config = report["run_inputs"]["config"]
    root, output = Path(config["temp_dir"]).resolve(), Path(output_dir).resolve()
    if not output.is_relative_to(root):
        raise ValueError("Evidence export must stay inside the authorized temporary root")
    output.mkdir(parents=True, exist_ok=True)
    exported = []
    for video, findings in report["finding_details"].items():
        if Path(video).name != video or "\\" in video:
            raise ValueError("Invalid video filename")
        if not findings:
            continue
        with VideoReader(Path(videos_dir) / video) as reader:
            for finding in findings:
                if len(exported) >= max_examples:
                    break
                images = []
                for i, frame in enumerate(finding["evidence"]):
                    actual = reader.at(frame["timestamp_sec"], max_side=config["max_side"])
                    jpeg = actual.jpeg()
                    if hashlib.sha256(jpeg).hexdigest() != frame["sha256"]:
                        raise ValueError("Reconstructed frame differs from the actual evaluated evidence")
                    path = output / f"finding_{len(exported) + 1:03d}_frame_{i + 1}.jpg"
                    path.write_bytes(jpeg)
                    images.append({**frame, "path": str(path)})
                exported.append({"video": video, **finding, "evidence": images})
        if len(exported) >= max_examples:
            break
    index = output / "finding_examples.json"
    index.write_text(json.dumps(exported, ensure_ascii=False, indent=2), encoding="utf-8")
    return exported


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation", default="backend/data/reports/evaluation.json")
    parser.add_argument("--videos-dir", default="dataset/videos")
    parser.add_argument("--output-dir", default="backend/analyzer/output/review_evidence")
    parser.add_argument("--max-examples", type=int, default=5)
    args = parser.parse_args()
    if args.max_examples < 1:
        parser.error("max-examples must be positive")
    examples = export_evidence(json.loads(Path(args.evaluation).read_text(encoding="utf-8")),
        args.videos_dir, args.output_dir, args.max_examples)
    print(json.dumps({"examples": len(examples), "jpeg_frames": sum(len(f["evidence"]) for f in examples),
                      "index": str(Path(args.output_dir) / "finding_examples.json")}))


if __name__ == "__main__":
    main()
