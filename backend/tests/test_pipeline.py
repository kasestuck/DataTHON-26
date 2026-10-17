from dataclasses import replace
import json
import time

import pytest

from backend.analyzer import EvidenceFrame, Finding, analyze_video
from backend.analyzer.catalog import load_catalog
from backend.analyzer.config import AnalyzerConfig
from backend.analyzer.detector import MockDetector
from backend.analyzer.errors import AnalysisTimeoutError, InferenceError, ModelNotConfiguredError, VideoReadError
from backend.analyzer.pipeline import Analyzer, _supervise, _worker_error, analyze_video_report, deduplicate
from backend.analyzer.vlm import MockVLM


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"synthetic": {"name": "Synthetic", "description": "Synthetic action"}}), encoding="utf-8")
    return AnalyzerConfig(catalog_path=str(path))


def test_mock_never_automatic(synthetic_video, config, monkeypatch):
    with pytest.raises(ModelNotConfiguredError, match="Mock"):
        Analyzer(MockDetector(), MockVLM(), config)
    monkeypatch.setenv("ANALYZER_DETECTOR_DIR", "")
    monkeypatch.setenv("ANALYZER_VLM_DIR", "")
    with pytest.raises(ModelNotConfiguredError):
        analyze_video(str(synthetic_video))
    with pytest.raises(VideoReadError):
        analyze_video("missing.mov")


def test_incomplete_weights_fail_before_inference(synthetic_video, config, tmp_path):
    incomplete = tmp_path / "incomplete-model"
    incomplete.mkdir()
    (incomplete / "config.json").write_text("{}")
    with pytest.raises(ModelNotConfiguredError, match="incomplete"):
        analyze_video_report(str(synthetic_video), replace(config, detector_dir=str(incomplete), vlm_dir=str(incomplete)))


def test_public_corrupt_video_is_rejected_before_model_loading(config, tmp_path):
    bad = tmp_path / "broken.mov"
    bad.write_bytes(b"synthetic invalid video")
    unused = tmp_path / "unused-model-fixture"
    unused.mkdir()
    (unused / "config.json").write_text("{}")
    (unused / "model.safetensors").write_bytes(b"synthetic unused weights")
    with pytest.raises(VideoReadError):
        analyze_video_report(str(bad), replace(config, detector_dir=str(unused), vlm_dir=str(unused), timeout_sec=5))


def test_end_to_end_synthetic_only(synthetic_video, config):
    response = dict(observed_action="synthetic observation", has_violation=True,
        violation_type="synthetic", timecode_sec=1., confidence=.9, evidence_indices=[1, 2])
    analyzer = Analyzer(MockDetector(), MockVLM(response), config, allow_mock=True)
    report = analyzer.analyze(str(synthetic_video))
    assert report.is_mock and len(report.findings) == 1
    finding = report.findings[0]
    assert finding.violation_type == "synthetic" and len(finding.evidence_frames) == 2
    assert report.sampled_frames == 6
    assert all(value >= 0 for value in report.timings.values())
    assert finding.evidence_frames[0].jpeg_bytes.startswith(b"\xff\xd8")
    response["timecode_sec"] = 50.
    with pytest.raises(InferenceError, match="outside"):
        Analyzer(MockDetector(), MockVLM(response), config, allow_mock=True).analyze(str(synthetic_video))


def test_empty_catalog_diagnostic_and_negative(synthetic_video, config, tmp_path):
    path = tmp_path / "empty.json"
    path.write_text('{}', encoding="utf-8")
    report = Analyzer(MockDetector(), MockVLM(), replace(config, catalog_path=str(path)), allow_mock=True).analyze(str(synthetic_video))
    assert report.findings == [] and report.diagnostics
    assert Analyzer(MockDetector(), MockVLM(), config, allow_mock=True).analyze(str(synthetic_video)).findings == []


def test_threshold_dedup_contract(config, synthetic_video):
    response = dict(observed_action="synthetic", has_violation=True, violation_type="synthetic",
        timecode_sec=1., confidence=.6, evidence_indices=[1])
    assert Analyzer(MockDetector(), MockVLM(response), config, allow_mock=True).analyze(str(synthetic_video)).findings == []
    evidence = [EvidenceFrame(1., b"synthetic-jpeg")]
    first = Finding("synthetic", 1., "test", .8, evidence)
    second = Finding("synthetic", 2., "test", .9, evidence)
    assert deduplicate([first, second], 4) == [second]
    with pytest.raises(ValueError):
        Finding("unknown", 1, "x", .9, evidence)
    with pytest.raises(ValueError):
        Finding("synthetic", 1, "x", .9, [])
    with pytest.raises(ValueError):
        EvidenceFrame(-1, b"x")


def _hung_worker(connection):
    time.sleep(10)


def _crashed_worker(connection):
    connection.close()


def _successful_worker(connection):
    connection.send((True, "synthetic result"))
    connection.close()


def _error_worker(connection):
    connection.send((False, "VideoReadError", "synthetic decode error"))
    connection.close()


def test_process_timeout_and_crash():
    started = time.monotonic()
    with pytest.raises(AnalysisTimeoutError):
        _supervise(_hung_worker, (), .2)
    assert time.monotonic() - started < 5
    with pytest.raises(InferenceError, match="without a result"):
        _supervise(_crashed_worker, (), 5)
    assert _supervise(_successful_worker, (), 5) == "synthetic result"
    with pytest.raises(VideoReadError, match="synthetic"):
        _supervise(_error_worker, (), 5)


def test_oom_is_error_not_negative(config, synthetic_video):
    class FailingVLM(MockVLM):
        def infer(self, *_):
            raise InferenceError("Synthetic CUDA out of memory")

    with pytest.raises(InferenceError, match="memory"):
        Analyzer(MockDetector(), FailingVLM(), config, allow_mock=True).analyze(str(synthetic_video))


def test_unexamined_short_video_is_not_a_negative(config, tmp_path):
    import cv2
    import numpy as np

    path = tmp_path / "too_short.mov"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 64))
    assert writer.isOpened()
    writer.write(np.zeros((64, 64, 3), np.uint8))
    writer.release()
    with pytest.raises(VideoReadError, match="too few"):
        Analyzer(MockDetector(), MockVLM(), config, allow_mock=True).analyze(str(path))


def test_config_and_catalog_validation(tmp_path):
    for kwargs in ({"sample_fps": 3}, {"timeout_sec": 301}, {"window_frames": 9}, {"confidence_threshold": float("nan")}, {"memory_fraction": 0}):
        with pytest.raises(ValueError):
            AnalyzerConfig(**kwargs)
    path = tmp_path / "invalid.json"
    path.write_text('{"schema_version":1,"types":[{"id":"unknown"}]}')
    with pytest.raises(ValueError):
        load_catalog(path)


def test_model_load_oom_is_normalized_for_callers():
    assert _worker_error(MemoryError())[1] == "InferenceError"
    oom = type("OutOfMemoryError", (RuntimeError,), {})
    assert _worker_error(oom("synthetic CUDA allocation"))[1] == "InferenceError"
