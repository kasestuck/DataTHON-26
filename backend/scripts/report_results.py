"""Export private per-video audit tables from real evaluation results."""
import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path

from backend.analyzer.labels import parse_verdict, read_labels
from backend.analyzer.metrics import match_events


def audit_rows(labels, report):
    grouped = defaultdict(list)
    for row in labels:
        grouped[row["video"]].append(row)
    skipped = {row["video"]: row for row in report["skipped"]}
    reasons = {row["video"]: row["reasons"] for row in report["manual_review"]}
    rows = []
    for video, source in grouped.items():
        actual = report["predictions"].get(video)
        positive = [row for row in source if parse_verdict(row["has_violation"])]
        timed = [row for row in positive if row["timecode_sec"] is not None]
        time_errors = [abs(timed[i]["timecode_sec"] - actual[j]["timecode_sec"])
            for i, j in match_events(timed, actual, float("inf"))] if actual is not None else []
        review = list(reasons.get(video, []))
        if any(row.get("review_status") == "needs_review" for row in source):
            review.append("annotation_needs_review")
        error = skipped.get(video, {})
        timing = report["timings"].get(video, error)
        rows.append({"video": video, "expected_verdict": str(bool(positive)).lower(),
            "expected_timecodes_sec": json.dumps([r["timecode_sec"] for r in positive]),
            "expected_types": json.dumps([r["violation_type"] for r in positive]),
            "proposed_types": json.dumps([r.get("proposed_type", "") for r in positive]),
            "review_status": ";".join(dict.fromkeys(r.get("review_status", "") for r in source)),
            "predicted_verdict": "" if actual is None else str(bool(actual)).lower(),
            "predicted_types": json.dumps([r["violation_type"] for r in actual or []]),
            "predicted_timecodes_sec": json.dumps([r["timecode_sec"] for r in actual or []]),
            "nearest_time_errors_sec": json.dumps(time_errors),
            "jpeg_counts": json.dumps([len(r.get("evidence", [])) for r in actual or []]),
            "wall_sec": timing.get("wall_sec", ""),
            "status": "evaluated" if actual is not None else "error" if error else "unevaluated",
            "error": error.get("reason", ""), "review_reasons": ";".join(dict.fromkeys(review))})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation", default="backend/data/reports/evaluation.json")
    parser.add_argument("--labels", default="backend/data/labels.csv")
    parser.add_argument("--output-dir", default="backend/data/reports")
    args = parser.parse_args()
    report = json.loads(Path(args.evaluation).read_text(encoding="utf-8"))
    if hashlib.sha256(Path(args.labels).read_bytes()).hexdigest() != report["run_inputs"]["labels_sha256"]:
        raise ValueError("Current labels differ from evaluated labels")
    rows = audit_rows(read_labels(args.labels), report)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    reviews = [row for row in rows if row["review_reasons"]]
    for name, values in (("evaluation_videos.csv", rows), ("evaluation_review.csv", reviews)):
        with (output / name).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(values)
    print(json.dumps({"videos": len(rows), "review_videos": len(reviews), "directory": str(output)}))


if __name__ == "__main__":
    main()
