import json

import cv2
import numpy as np
import pytest

from backend.analyzer.contracts import EvidenceFrame, Finding
from backend.analyzer.pipeline import AnalysisReport
from backend.scripts.evaluate import evaluate, verify_findings


def finding(data=None):
    if data is None:
        ok, image = cv2.imencode(".jpg", np.zeros((32, 64, 3), np.uint8))
        assert ok
        data = image.tobytes()
    return Finding("synthetic", 1., "Synthetic observation", .9, [EvidenceFrame(1., data)])


def test_actual_jpeg_and_catalog_boundary():
    actual = finding()
    result = verify_findings([actual], {"synthetic"})
    assert result[0]["evidence"][0]["jpeg_bytes"] > 0
    assert len(result[0]["evidence"][0]["sha256"]) == 64
    assert "description" not in result[0]
    with pytest.raises(ValueError, match="catalog"):
        verify_findings([actual], {"another"})
    with pytest.raises(ValueError, match="JPEG"):
        verify_findings([finding(b"\xff\xd8invalid\xff\xd9")], {"synthetic"})


def test_evaluation_checkpoint_resume_and_provenance(tmp_path):
    rows = [{"video": "synthetic.mov", "has_violation": "true", "timecode_sec": 1., "violation_type": "synthetic"}]
    checkpoint = tmp_path / "checkpoint.json"
    report = evaluate(rows, tmp_path, runner=lambda _: AnalysisReport(findings=[finding()], timings={"total_sec": .01}),
        valid_types={"synthetic"}, checkpoint=checkpoint, fingerprint="synthetic-fixture")
    assert report["classification"]["micro"]["f1"] == 1.
    assert report["runtime"]["evaluated"] == 1
    assert json.loads(checkpoint.read_text())["predictions"] == report["predictions"]
    resumed = evaluate(rows, tmp_path, runner=lambda _: pytest.fail("must reuse identical checkpoint"),
        valid_types={"synthetic"}, checkpoint=checkpoint, fingerprint="synthetic-fixture", resume=True)
    assert resumed["predictions"] == report["predictions"]
    with pytest.raises(ValueError, match="does not match"):
        evaluate(rows, tmp_path, checkpoint=checkpoint, fingerprint="changed", resume=True)


def test_contract_failure_is_not_a_negative_prediction(tmp_path):
    rows = [{"video": "synthetic.mov", "has_violation": "true", "timecode_sec": 1., "violation_type": "synthetic"}]
    result = evaluate(rows, tmp_path, runner=lambda _: AnalysisReport(findings=[finding()]), valid_types={"other"})
    assert result["status"] == "unavailable" and result["video_detection"] is None
    assert result["skipped"][0]["reason"] == "ValueError"
