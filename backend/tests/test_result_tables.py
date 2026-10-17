import json

from backend.scripts.report_results import audit_rows


def test_audit_table_preserves_unavailable_and_annotation_review():
    labels = [dict(video="a.mov", has_violation="true", timecode_sec=10., violation_type="unknown",
                   proposed_type="synthetic", review_status="needs_review"),
              dict(video="b.mov", has_violation="false", timecode_sec="", violation_type="", review_status="negative"),
              dict(video="c.mov", has_violation="false", timecode_sec="", violation_type="", review_status="negative")]
    report = {"predictions": {"a.mov": [{"timecode_sec": 14., "violation_type": "synthetic", "evidence": [{}, {}]}]},
        "timings": {"a.mov": {"wall_sec": 2.}}, "skipped": [{"video": "b.mov", "reason": "InferenceError", "wall_sec": 1.}],
        "manual_review": [{"video": "a.mov", "reasons": ["localization"]}]}
    rows = audit_rows(labels, report)
    assert json.loads(rows[0]["nearest_time_errors_sec"]) == [4.]
    assert rows[0]["review_reasons"] == "localization;annotation_needs_review"
    assert rows[1]["status"] == "error" and rows[1]["predicted_verdict"] == ""
    assert rows[2]["status"] == "unevaluated" and rows[2]["predicted_verdict"] == ""
